using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Architecture;
using Autodesk.Revit.DB.Mechanical;
using Autodesk.Revit.DB.Structure;
using System.Text.Json;

namespace Ac2Revit;

public sealed class Importer
{
    readonly Document doc;
    readonly Manifest manifest;
    readonly ImportConfig config;
    readonly Geometry geometry;
    readonly string project;
    readonly Dictionary<string, Element> existing;
    readonly Dictionary<string, Level> levels = new();
    readonly Dictionary<string, HostObjAttributes> types = new();
    readonly Dictionary<string, Material> materials = new(StringComparer.OrdinalIgnoreCase);
    readonly Dictionary<string, Element> hosts = new(StringComparer.OrdinalIgnoreCase);
    readonly List<FamilySymbol> symbols;
    readonly BasicColumnAdapter basicColumns;
    string columnPreparationError = "";
    public Report Report { get; }
    public Action<string, int, int>? Progress { get; set; }

    public Importer(Document doc, Manifest manifest, ImportConfig config)
    {
        if (doc.IsFamilyDocument || doc.IsReadOnly) throw new InvalidOperationException("An editable project document is required");
        this.doc = doc; this.manifest = manifest; this.config = config;
        geometry = new Geometry(doc, manifest.Coordinates.Transform);
        project = Guid.Parse(manifest.Source.ProjectId).ToString("D");
        existing = Storage.Index(doc, project);
        foreach(var pair in existing)hosts[pair.Key]=pair.Value;
        foreach(var record in manifest.Levels)
            if(existing.GetValueOrDefault(record.Key) is Level level)levels[record.Key]=level;
        foreach(var record in manifest.Types)
            if(existing.GetValueOrDefault(record.Key) is HostObjAttributes type)types[record.Key]=type;
        foreach(var pair in existing.Where(p=>p.Key.StartsWith("material:",StringComparison.OrdinalIgnoreCase)))
            if(pair.Value is Material material)materials[pair.Key[9..]]=material;
        symbols = new FilteredElementCollector(doc).OfClass(typeof(FamilySymbol)).Cast<FamilySymbol>().ToList();
        Report = new() { ProjectId = project };
        basicColumns = new(doc, config, geometry, manifest.Coordinates.Transform, project, Report);
    }
    string ElementHash(ElementRecord e) => Json.Hash(new { e.ContentHash, e.Kind, e.LevelKey, e.Mark, e.Dependencies,
        e.Status, e.Payload, e.Raw, manifest.Coordinates.Transform, Config = config });
    static string Name(string value, string key)
    {
        var clean = new string(value.Select(c => "\\:{}[]|;<>?`~".Contains(c) || char.IsControl(c) ? '_' : c).ToArray());
        return "AC " + clean[..Math.Min(clean.Length, 100)] + " " + Json.Hash(key)[..12];
    }
    bool Existing(string key, string hash, string kind, out Element? element)
    {
        element = null;
        if (!existing.TryGetValue(key, out var found)) return false;
        var tag = Storage.Read(found)!.Value;
        if (tag.Hash != hash)
        {
            Report.Add(key, kind, "conflict", "Source/configuration changed. Existing element retained; explicit update adapter required.", found.UniqueId);
            return true;
        }
        element = found;
        Report.Add(key, kind, "unchanged", "Existing tagged element retained. Local Revit edits are not overwritten.", found.UniqueId);
        return true;
    }
    Element? Commit(string key, string kind, string hash, Func<Element> action, string raw = "")
    {
        using var transaction = new Transaction(doc, "AC " + kind + " " + key);
        try
        {
            transaction.Start();
            var options = transaction.GetFailureHandlingOptions();
            options.SetFailuresPreprocessor(new Failures(Report, key, kind));
            options.SetClearAfterRollback(true); transaction.SetFailureHandlingOptions(options);
            var element = action();
            Storage.Tag(element, project, key, hash, raw);
            doc.Regenerate();
            if (transaction.Commit() != TransactionStatus.Committed) throw new InvalidOperationException("Transaction rolled back by Revit failure processing");
            existing[key] = element;
            Report.Add(key, kind, "created", "", element.UniqueId);
            return element;
        }
        catch (Exception ex)
        {
            if (transaction.GetStatus() == TransactionStatus.Started) transaction.RollBack();
            Report.Add(key, kind, "failed", ex.Message);
            if (!config.AllowPartial) throw;
            return null;
        }
    }
    public Report Preview()
    {
        Report.Mode = "preview";
        foreach (var l in manifest.Levels) Report.Add(l.Key, "Level", "planned", $"{l.Name}: {l.Elevation} m");
        foreach (var t in manifest.Types)
        {
            string? error = ConstructionError(t);
            Report.Add(t.Key, t.Kind + "Type", error == null ? "planned" : "blocked", error ?? "");
        }
        foreach (var e in manifest.OrderedElements())
        {
            if (Existing(e.SourceGuid, ElementHash(e), e.Kind, out _)) continue;
            try
            {
                ValidateElement(e);
                Report.Add(e.SourceGuid, e.Kind, "planned");
            }
            catch (Exception ex) { Report.Add(e.SourceGuid, e.Kind, "blocked", ex.Message); }
        }
        return Report;
    }
    public Report Run(ISet<string>? kinds=null,bool prepareResources=true,bool finalizeJoins=true)
    {
        Report.Mode = "import";
        using var group = new TransactionGroup(doc, "Archicad JSON import");
        group.Start();
        try
        {
            if(prepareResources)
            {
                Progress?.Invoke("levels", 0, manifest.Levels.Count); Levels();
                Progress?.Invoke("materials", 0, manifest.Materials.Count); Materials();
                Progress?.Invoke("types", 0, manifest.Types.Count); Types();
            }
            if(config.AutomaticSimpleColumns && (kinds==null || kinds.Contains("Column")))
            {
                try { basicColumns.Prepare(manifest.Elements.Where(e=>e.Kind=="Column")); }
                catch(Exception ex) { columnPreparationError=ex.ToString();Report.Add("column-adapter","ColumnFamily","failed",columnPreparationError);if(!config.AllowPartial)throw; }
            }
            var ordered = manifest.OrderedElements().Where(e=>kinds==null || kinds.Contains(e.Kind)).ToList();
            int processed = 0;
            bool wallsConnected = false;
            foreach (var e in ordered)
            {
                // All physical hosts are ready before the first spatial element is evaluated.
                if (!wallsConnected && e.Kind == "Zone")
                {
                    if (config.ConnectWalls && finalizeJoins) ConnectWalls();
                    wallsConnected = true;
                }
                if (processed++ % 50 == 0) Progress?.Invoke("elements", processed-1, ordered.Count);
                if (Existing(e.SourceGuid, ElementHash(e), e.Kind, out var old))
                {
                    if (old != null) hosts[e.SourceGuid] = old;
                    continue;
                }
                try
                {
                    ValidateElement(e);
                    if (!levels.ContainsKey(e.LevelKey)) throw new InvalidOperationException("Home story was not imported");
                    if (e.Dependencies.Any(h => !hosts.ContainsKey(h))) throw new InvalidOperationException("Host failed/blocked");
                    var element = Commit(e.SourceGuid, e.Kind, ElementHash(e), () => Create(e), e.Raw.GetRawText());
                    if (element != null) hosts[e.SourceGuid] = element;
                }
                catch (Exception ex)
                {
                    Report.Add(e.SourceGuid, e.Kind, "blocked", ex.Message);
                    if (!config.AllowPartial) throw;
                }
            }
            if (!wallsConnected && config.ConnectWalls && finalizeJoins) ConnectWalls();
            group.Assimilate();
        }
        catch
        {
            group.RollBack();
            // Previously committed inner transactions are also undone by the group.
            for (int i = 0; i < Report.Items.Count; i++)
                if (Report.Items[i].Status == "created") Report.Items[i] = Report.Items[i] with { Status = "rolledBack", RevitUniqueId = "" };
            throw;
        }
        return Report;
    }
    public Report JoinSourceWalls()
    {
        ConnectWalls();return Report;
    }
    public Report RebuildSpatialElements()
    {
        // The user requested a fresh evaluation after physical hosts/openings.
        // Delete only owned rooms/spaces, refusing dependent user annotations.
        using(var t=new Transaction(doc,"Re-evaluate imported rooms after physical model"))
        {
            t.Start();
            foreach(var record in manifest.Elements.Where(e=>e.Kind=="Zone"))
                if(existing.GetValueOrDefault(record.SourceGuid) is SpatialElement spatial)
                {
                    var id=spatial.Id;
                    var allowed=spatial.GetDependentElements(null).Select(id=>doc.GetElement(id)).Where(e=>e!=null && e.GetType()==typeof(Element)
                        && e.Category==null && string.IsNullOrEmpty(e.Name) && !e.ViewSpecific && Storage.Read(e)==null
                        && e.Parameters.Cast<Parameter>().All(p=>p.IsReadOnly) && e.get_BoundingBox(null)==null).Select(e=>e.Id).ToHashSet();
                    var removed=doc.Delete(id);
                    if(removed.Any(other=>other!=id && !allowed.Contains(other)))throw new InvalidDataException("Refreshing room would remove dependent user data: "+record.SourceGuid);
                    existing.Remove(record.SourceGuid);hosts.Remove(record.SourceGuid);
                }
            t.Commit();
        }
        return Run(new HashSet<string>{"Zone"},false,true);
    }
    /// <summary>Diagnostic repair of a COPY: verify retained geometry, create missing walls,
    /// join source walls and recreate only tagged source spatial elements.</summary>
    public Report RepairCopy()
    {
        Report.Mode="repairCopy";
        foreach(var l in manifest.Levels)
            if(existing.TryGetValue(l.Key,out var element) && element is Level level)levels[l.Key]=level;
            else throw new InvalidDataException("Repair requires existing source level: "+l.Key);
        foreach(var m in manifest.Materials.Where(m=>m.TryGetProperty("attributeId",out _)))
        {
            var key=m.GetProperty("attributeId").S("guid").ToUpperInvariant();
            if(existing.TryGetValue("material:"+key,out var element) && element is Material material)materials[key]=material;
        }
        foreach(var t in manifest.Types)
            if(existing.TryGetValue(t.Key,out var element) && element is HostObjAttributes type)types[t.Key]=type;
        var referenceCorrections=new List<(ElementRecord Record,Wall Wall,Curve Curve)>();
        foreach(var e in manifest.Elements.Where(e=>e.Kind=="Wall"))
        {
            if(!existing.TryGetValue(e.SourceGuid,out var element))continue;
            if(element is not Wall wall || e.Status=="unsupported")throw new InvalidDataException("Unexpected retained source wall: "+e.SourceGuid);
            var p=e.Payload;var level=levels[e.LevelKey];var expected=geometry.Curve(p.GetProperty("curve").Read<Edge>(),level.Elevation);
            var legacy=expected;
            expected=CentreCurve(wall,expected,p);
            var actual=((LocationCurve)wall.Location).Curve;
            double tolerance=Geometry.Feet(0.001);
            bool near(XYZ a,XYZ b)=>new XYZ(a.X-b.X,a.Y-b.Y,0).GetLength()<=tolerance;
            bool matches(Curve c)=>near(actual.GetEndPoint(0),c.GetEndPoint(0)) && near(actual.GetEndPoint(1),c.GetEndPoint(1)) && near(actual.Evaluate(0.5,true),c.Evaluate(0.5,true));
            bool corrected=matches(expected),oldConvention=matches(legacy);
            if((!corrected && !oldConvention) ||
               Math.Abs(wall.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM).AsDouble()-Geometry.Feet(p.D("height")))>tolerance ||
               Math.Abs(wall.get_Parameter(BuiltInParameter.WALL_BASE_OFFSET).AsDouble()-Geometry.Feet(p.D("bottomOffset")))>tolerance ||
               wall.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT).AsElementId()!=level.Id || wall.Flipped!=p.B("flipped") ||
               wall.get_Parameter(BuiltInParameter.WALL_KEY_REF_PARAM).AsInteger()!=(int)LocationLine(p.S("referenceLine")))
                throw new InvalidDataException("Retained wall differs from source; repair stopped: "+e.SourceGuid);
            if(!types.TryGetValue(p.S("typeKey"),out var target) || wall.GetTypeId()!=target.Id)
                throw new InvalidDataException("Retained wall type differs from source: "+e.SourceGuid);
            hosts[e.SourceGuid]=wall;Report.Add(e.SourceGuid,"Wall","retained","Source curve, level, offset, height, flip and reference line verified",wall.UniqueId);
            if(!corrected)referenceCorrections.Add((e,wall,expected));
        }
        using var group=new TransactionGroup(doc,"Repair copied Archicad model");group.Start();
        try
        {
            if(referenceCorrections.Count>0)
            {
                using var t=new Transaction(doc,"Correct source reference planes");t.Start();int count=0;
                var options=t.GetFailureHandlingOptions();options.SetFailuresPreprocessor(new Failures(Report,"reference-planes","WallBinding"));options.SetClearAfterRollback(true);t.SetFailureHandlingOptions(options);
                foreach(var correction in referenceCorrections)
                {
                    WallUtils.DisallowWallJoinAtEnd(correction.Wall,0);WallUtils.DisallowWallJoinAtEnd(correction.Wall,1);
                    ((LocationCurve)correction.Wall.Location).Curve=correction.Curve;
                    if(count++%100==0)Progress?.Invoke("referencePlanes",count,referenceCorrections.Count);
                }
                doc.Regenerate();if(t.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Reference-plane correction rolled back");
                Report.Add("reference-planes","WallBinding","corrected",$"Corrected centre curves for {referenceCorrections.Count} walls using CompoundStructure location-line offset.");
            }
            var wallRecords=manifest.OrderedElements().Where(e=>e.Kind=="Wall").ToList();int done=0;
            foreach(var e in wallRecords)
            {
                if(done++%50==0)Progress?.Invoke("missingWalls",done-1,wallRecords.Count);
                if(hosts.ContainsKey(e.SourceGuid))continue;
                try
                {
                    ValidateElement(e);
                    var wall=Commit(e.SourceGuid,e.Kind,ElementHash(e),()=>Create(e),e.Raw.GetRawText());
                    if(wall!=null)hosts[e.SourceGuid]=wall;
                }
                catch(Exception ex){Report.Add(e.SourceGuid,e.Kind,"blocked",ex.Message);if(!config.AllowPartial)throw;}
            }
            // The operation is restricted to a generated copy; user/template spatial elements are retained.
            var sourceZones=manifest.Elements.Where(e=>e.Kind=="Zone").Select(e=>e.SourceGuid).ToHashSet(StringComparer.OrdinalIgnoreCase);
            using(var t=new Transaction(doc,"Recalculate source spatial elements"))
            {
                t.Start();
                foreach(var key in sourceZones)
                    if(existing.TryGetValue(key,out var element) && element is SpatialElement)
                    { doc.Delete(element.Id);existing.Remove(key); }
                t.Commit();
            }
            if(config.ConnectWalls)ConnectWalls();
            var zones=manifest.OrderedElements().Where(e=>e.Kind=="Zone").ToList();done=0;
            foreach(var e in zones)
            {
                if(done++%10==0)Progress?.Invoke("zoneAreas",done-1,zones.Count);
                try{ValidateElement(e);Commit(e.SourceGuid,e.Kind,ElementHash(e),()=>Create(e),e.Raw.GetRawText());}
                catch(Exception ex){Report.Add(e.SourceGuid,e.Kind,"blocked",ex.Message);if(!config.AllowPartial)throw;}
            }
            group.Assimilate();
        }
        catch
        {
            group.RollBack();
            for(int i=0;i<Report.Items.Count;i++)if(Report.Items[i].Status=="created")Report.Items[i]=Report.Items[i] with{Status="rolledBack",RevitUniqueId=""};
            throw;
        }
        return Report;
    }
    void Levels()
    {
        foreach (var l in manifest.Levels)
        {
            var hash = Json.Hash(new { l, manifest.Coordinates.Transform.Translation.Z });
            if (Existing(l.Key, hash, "Level", out var old))
            {
                if (old is Level level) levels[l.Key] = level;
                continue;
            }
            var created = Commit(l.Key, "Level", hash, () => {
                var level = Level.Create(doc, Geometry.Feet(l.Elevation + manifest.Coordinates.Transform.Translation.Z));
                level.Name = Name(l.Name, project + l.Key); return level;
            });
            if (created is Level result) levels[l.Key] = result;
        }
    }
    void ConnectWalls()
    {
        Progress?.Invoke("wallTopology",0,hosts.Values.Count(e=>e is Wall));
        new WallConnector(doc,config,Report,manifest.Coordinates.Transform,Progress).Run(hosts,manifest.Elements);
    }
    void Materials()
    {
        foreach (var m in manifest.Materials.Where(m => m.TryGetProperty("attributeId", out _)))
        {
            string guid = m.GetProperty("attributeId").S("guid").ToUpperInvariant(), key = "material:" + guid;
            if (Existing(key, Json.Hash(m), "Material", out var old))
            {
                if (old is Material mat) materials[guid] = mat;
                continue;
            }
            var created = Commit(key, "Material", Json.Hash(m), () => {
                var mat = (Material)doc.GetElement(Material.Create(doc, Name(m.S("name"), project + key)));
                mat.MaterialClass = "Archicad Building Material";
                return mat;
            }, m.GetRawText());
            if (created is Material result) materials[guid] = result;
        }
    }
    string? ConstructionError(TypeRecord t)
    {
        double min = CompoundStructure.GetMinimumLayerThickness();
        if (t.Layers.Any(l => l.Function != "Membrane" && Geometry.Feet(l.Width) < min)) return $"Layer below Revit minimum ({UnitUtils.ConvertFromInternalUnits(min, UnitTypeId.Millimeters):G6} mm). No automatic thickening.";
        var cores = t.Layers.Select((l, i) => (l, i)).Where(x => x.l.Function == "Core").Select(x => x.i).ToList();
        // Archicad legitimately permits all-Other/Finish composites. Revit's
        // technical core boundary is independent of Structural function.
        if (cores.Count == 0) return null;
        if (cores.Count != cores[^1]-cores[0]+1) return "Non-contiguous core";
        return null;
    }
    void Types()
    {
        var baseWall = new FilteredElementCollector(doc).OfClass(typeof(WallType)).Cast<WallType>().FirstOrDefault(w => w.Kind == WallKind.Basic);
        var baseFloor = new FilteredElementCollector(doc).OfClass(typeof(FloorType)).Cast<FloorType>().FirstOrDefault(t => !t.IsFoundationSlab);
        foreach (var t in manifest.Types)
        {
            var hash = Json.Hash(t);
            if (Existing(t.Key, hash, t.Kind + "Type", out var old))
            {
                if (old is HostObjAttributes type) types[t.Key] = type;
                continue;
            }
            var error = ConstructionError(t);
            if (error != null) { Report.Add(t.Key, t.Kind + "Type", "blocked", error); if (!config.AllowPartial) throw new InvalidDataException(error); continue; }
            var created = Commit(t.Key, t.Kind + "Type", hash, () => {
                var (first,last)=CoreBounds(t);
                var layers = t.Layers.Select((l, i) => new CompoundStructureLayer(Geometry.Feet(l.Width),
                    l.Function switch { "Core" => MaterialFunctionAssignment.Structure, "Membrane" => MaterialFunctionAssignment.Membrane, "Finish" => i < first ? MaterialFunctionAssignment.Finish1 : MaterialFunctionAssignment.Finish2, _ => MaterialFunctionAssignment.Substrate },
                    string.IsNullOrEmpty(l.MaterialKey) ? ElementId.InvalidElementId : materials.TryGetValue(l.MaterialKey, out var mat) ? mat.Id : throw new InvalidDataException("Building material missing: " + l.MaterialKey))).ToList();
                var baseType = t.Kind == "Wall" ? (HostObjAttributes?)baseWall : baseFloor;
                if (baseType == null) throw new InvalidOperationException("Template has no base " + t.Kind + " type");
                var structure = CompoundStructure.CreateSimpleCompoundStructure(layers);
                // CreateSimpleCompoundStructure has wall wrapping defaults. Floor types reject them.
                using var templateStructure = baseType.GetCompoundStructure();
                if (templateStructure != null)
                {
                    structure.EndCap = templateStructure.EndCap;
                    structure.OpeningWrapping = templateStructure.OpeningWrapping;
                }
                structure.SetNumberOfShellLayers(ShellLayerType.Exterior, first);
                structure.SetNumberOfShellLayers(ShellLayerType.Interior, t.Layers.Count-last-1);
                bool noSourceCore=!t.Layers.Any(l=>l.Function=="Core"),emptyCore=false;
                if(noSourceCore)
                {
                    // Revit explicitly supports an empty core. Prefer it to
                    // inventing a structural layer; keep every source function.
                    structure.SetNumberOfShellLayers(ShellLayerType.Interior,0);
                    structure.SetNumberOfShellLayers(ShellLayerType.Exterior,layers.Count);
                    emptyCore=structure.IsValid(doc,out _,out _);
                    if(!emptyCore)
                    {
                        structure.SetNumberOfShellLayers(ShellLayerType.Exterior,first);
                        structure.SetNumberOfShellLayers(ShellLayerType.Interior,layers.Count-last-1);
                    }
                }
                if(!structure.IsValid(doc,out var errors,out _))throw new InvalidDataException("Invalid compound structure: "+string.Join(", ",errors.Select(p=>p.Key+":"+p.Value)));
                var duplicate = (HostObjAttributes)baseType.Duplicate(Name(t.Name, project + t.Key));
                duplicate.SetCompoundStructure(structure);
                using var actual=duplicate.GetCompoundStructure();
                if(actual.LayerCount!=layers.Count || Enumerable.Range(0,layers.Count).Any(i=>Math.Abs(actual.GetLayerWidth(i)-layers[i].Width)>1e-9 || actual.GetLayerFunction(i)!=layers[i].Function))
                    throw new InvalidDataException("Revit altered source construction layers");
                if(noSourceCore)
                    Report.Add(t.Key,t.Kind+"Type",emptyCore?"notice":"warning",emptyCore?"Source has no Core. Revit empty core accepted; layer functions, widths and materials preserved.":"Source has no Core. Technical Revit core spans non-finish layers; functions, widths and materials preserved. No load-bearing layer invented; core semantics require review.");
                return duplicate;
            });
            if (created is HostObjAttributes result) types[t.Key] = result;
        }
    }
    static (int First,int Last) CoreBounds(TypeRecord type)
    {
        var indices=type.Layers.Select((l,i)=>(l,i)).Where(x=>x.l.Function=="Core").Select(x=>x.i).ToList();
        if(indices.Count==0)indices=type.Layers.Select((l,i)=>(l,i)).Where(x=>x.l.Function is not ("Finish" or "Membrane")).Select(x=>x.i).ToList();
        if(indices.Count==0)indices=[type.Layers.Select((l,i)=>(l,i)).Where(x=>x.l.Function!="Membrane").OrderByDescending(x=>x.l.Width).First().i];
        return (indices[0],indices[^1]);
    }
    FamilyMapping Mapping(ElementRecord e)
    {
        var key = e.Kind + ":" + e.Payload.S("libraryGuid");
        if (!config.Families.TryGetValue(key, out var mapping) && !config.Families.TryGetValue(e.Kind + ":" + e.SourceGuid, out mapping))
            throw new InvalidDataException("No family mapping: " + key);
        if (!mapping.Calibrated) throw new InvalidDataException("Family placement/orientation must be calibrated first: " + key);
        return mapping;
    }
    FamilySymbol Symbol(ElementRecord e, FamilyMapping m)
    {
        var category = e.Kind switch { "Door" => BuiltInCategory.OST_Doors, "Window" => BuiltInCategory.OST_Windows,
            "Column" => BuiltInCategory.OST_StructuralColumns, "Beam" => BuiltInCategory.OST_StructuralFraming, _ => BuiltInCategory.INVALID };
        return symbols.SingleOrDefault(s => s.FamilyName == m.Family && s.Name == m.Type && (category == BuiltInCategory.INVALID || s.Category.Id == new ElementId(category)))
            ?? throw new InvalidDataException("Load mapped family/type with correct category: " + m.Family + " / " + m.Type);
    }
    void ValidateElement(ElementRecord e)
    {
        if (e.Status == "unsupported") throw new InvalidDataException(e.Reason);
        var p = e.Payload;
        if(e.Kind=="Grid")
        {
            var curve=geometry.Curve(p.GetProperty("curve").Read<Edge>(),0);
            if(curve is not Line && curve is not Arc)throw new InvalidDataException("Grid requires a line or circular arc");
            if(string.IsNullOrWhiteSpace(p.S("name")))throw new InvalidDataException("Grid label missing");
            return;
        }
        if(e.Kind=="Column" && config.AutomaticSimpleColumns)
        {
            if(columnPreparationError.Length>0)throw new InvalidDataException("Column preparation failed: "+columnPreparationError);
            basicColumns.Validate(e);return;
        }
        if (e.Kind is "Wall" or "Slab")
        {
            var t = manifest.Types.SingleOrDefault(t => t.Key == p.S("typeKey")) ?? throw new InvalidDataException("Missing type");
            string? error = ConstructionError(t); if (error != null) throw new InvalidDataException(error);
            if (e.Kind == "Wall")
            {
                _ = geometry.Curve(p.GetProperty("curve").Read<Edge>());
                if (p.D("height") <= 0) throw new InvalidDataException("Invalid height");
                _ = LocationLine(p.S("referenceLine"));
                if (Math.Abs(p.D("referenceOffset"))>1e-9)
                {
                    if(p.GetProperty("curve").S("kind")!="line")throw new InvalidDataException("Nonzero offset on arc requires radial calibration");
                    if(p.S("referenceLine") is "Center" or "CoreCenter")throw new InvalidDataException("Reference-line offset is not active on a center line");
                }
            }
            else _ = geometry.Loops(p.GetProperty("loops").Read<List<List<Edge>>>(), 0);
        }
        else if (e.Kind is "Door" or "Window")
        {
            var m = Mapping(e); var s = Symbol(e, m);
            if (s.Family.FamilyPlacementType != FamilyPlacementType.OneLevelBasedHosted) throw new InvalidDataException("Opening family must be wall hosted");
            if (p.D("width") <= 0 || p.D("height") <= 0) throw new InvalidDataException("Invalid dimensions");
        }
        else if (e.Kind == "Zone")
        {
            if (config.ZoneMode is not ("room" or "space")) throw new InvalidDataException("zoneMode must be room or space");
            _ = geometry.Xyz(p.GetProperty("point").Read<Point>());
        }
        else if (e.Kind is "Column" or "Beam" or "Object" or "Lamp")
        {
            var mapping = Mapping(e); var symbol = Symbol(e, mapping);
            var d = p.GetProperty("details");
            if (e.Kind is "Column" or "Beam")
            {
                if (d.B("isSlanted") || Math.Abs(d.D("arcAngle")) > 1e-10 || Math.Abs(d.D("verticalCurveHeight")) > 1e-10)
                    throw new InvalidDataException("Curved/slanted structural family requires an axis adapter");
                if (d.B("isFlipped") && !mapping.SymmetricSection)
                    throw new InvalidDataException("Flipped structural section requires a calibrated mirror adapter or declared symmetric section");
                if (!d.TryGetProperty("width", out _) || (e.Kind == "Column" && !d.TryGetProperty("depth", out _)))
                    throw new InvalidDataException("Segmented structural member requires segment adapters");
                if (e.Kind == "Column" && symbol.Family.FamilyPlacementType != FamilyPlacementType.TwoLevelsBased)
                    throw new InvalidDataException("Column family must be based on two levels");
                if (e.Kind == "Beam" && symbol.Family.FamilyPlacementType != FamilyPlacementType.CurveDrivenStructural)
                    throw new InvalidDataException("Beam family must be curve driven structural");
                if (d.TryGetProperty("holes", out var holes) && holes.GetArrayLength() > 0)
                    throw new InvalidDataException("Beam holes require a void adapter");
            }
            else
            {
                if (symbol.Family.FamilyPlacementType != FamilyPlacementType.OneLevelBased)
                    throw new InvalidDataException("Object family must be unhosted and based on one level");
                if (d.B("reflected") && !mapping.AllowMirroring)
                    throw new InvalidDataException("Reflected object requires calibrated mirror permission");
            }
        }
        else if (e.Kind == "Morph") ValidateMesh(p.GetProperty("mesh").Read<MeshRecord>());
        else throw new InvalidDataException("Native family/solid adapter not implemented for " + e.Kind + "; raw preserved");
    }
    static WallLocationLine LocationLine(string key) => key switch { "Center" => WallLocationLine.WallCenterline,
        "Outside" => WallLocationLine.FinishFaceExterior, "Inside" => WallLocationLine.FinishFaceInterior,
        "CoreOutside" => WallLocationLine.CoreExterior, "CoreInside" => WallLocationLine.CoreInterior,
        "CoreCenter" => WallLocationLine.CoreCenterline, _ => throw new InvalidDataException("Unknown reference line") };
    static Curve CentreCurve(Wall wall,Curve reference,JsonElement payload)
    {
        using var structure=wall.WallType.GetCompoundStructure();
        double keyOffset=structure.GetOffsetForLocationLine(LocationLine(payload.S("referenceLine")));
        double signed=Geometry.Feet((payload.S("referenceLine") is "Inside" or "CoreInside"?-1:1)*payload.D("referenceOffset"));
        double displacement=signed-keyOffset;
        if(Math.Abs(displacement)<1e-10)return reference;
        if(reference is Line)return reference.CreateTransformed(Transform.CreateTranslation(wall.Orientation*displacement));
        var right=reference.ComputeDerivatives(0,true).BasisX.Normalize().CrossProduct(XYZ.BasisZ).Normalize();
        double side=right.DotProduct(wall.Orientation);
        if(Math.Abs(side)<0.99999)throw new InvalidDataException("Reference normal is inconsistent with curve tangent");
        return reference.CreateOffset(displacement*Math.Sign(side),XYZ.BasisZ);
    }
    static void Set(Element e, BuiltInParameter parameter, double value)
    {
        var p = e.get_Parameter(parameter);
        if (p == null || p.IsReadOnly || !p.Set(value)) throw new InvalidOperationException("Cannot set " + parameter);
    }
    Element Create(ElementRecord e)
    {
        var p = e.Payload; var level = levels[e.LevelKey]; Element result;
        switch (e.Kind)
        {
            case "Grid":
                var gridCurve=geometry.Curve(p.GetProperty("curve").Read<Edge>(),0);
                var grid=gridCurve is Line line?Grid.Create(doc,line):Grid.Create(doc,(Arc)gridCurve);
                grid.Name=p.S("name");
                grid.SetVerticalExtents(levels.Values.Min(l=>l.Elevation)-Geometry.Feet(1),levels.Values.Max(l=>l.Elevation)+Geometry.Feet(1));
                result=grid;break;
            case "Wall":
                if (!types.TryGetValue(p.S("typeKey"), out var wt)) throw new InvalidDataException("Wall type blocked");
                var curve = geometry.Curve(p.GetProperty("curve").Read<Edge>(), level.Elevation);
                var wall = Wall.Create(doc, curve, wt.Id, level.Id, Geometry.Feet(p.D("height")), Geometry.Feet(p.D("bottomOffset")), p.B("flipped"), false);
                WallUtils.DisallowWallJoinAtEnd(wall, 0); WallUtils.DisallowWallJoinAtEnd(wall, 1);
                wall.get_Parameter(BuiltInParameter.WALL_KEY_REF_PARAM).Set((int)LocationLine(p.S("referenceLine")));
                wall.get_Parameter(BuiltInParameter.WALL_ATTR_ROOM_BOUNDING).Set(p.B("roomBounding", true) ? 1 : 0);
                doc.Regenerate();
                double referenceOffset = p.D("referenceOffset");
                Curve targetCurve = CentreCurve(wall,curve,p);
                ((LocationCurve)wall.Location).Curve = targetCurve;
                if(Math.Abs(referenceOffset)>1e-9 || targetCurve.GetEndPoint(0).DistanceTo(curve.GetEndPoint(0))>1e-8)
                {
                    doc.Regenerate();var box=wall.get_BoundingBox(null);
                    Point M(XYZ v)=>new(){X=UnitUtils.ConvertFromInternalUnits(v.X,UnitTypeId.Meters),Y=UnitUtils.ConvertFromInternalUnits(v.Y,UnitTypeId.Meters),Z=UnitUtils.ConvertFromInternalUnits(v.Z,UnitTypeId.Meters)};
                    Report.WallOffsets.Add(new(e.SourceGuid,p.S("referenceLine"),referenceOffset,M(curve.GetEndPoint(0)),M(targetCurve.GetEndPoint(0)),M(box.Min),M(box.Max)));
                }
                result = wall; break;
            case "Slab":
                if (!types.TryGetValue(p.S("typeKey"), out var ft)) throw new InvalidDataException("Floor type blocked");
                var loops = geometry.Loops(p.GetProperty("loops").Read<List<List<Edge>>>(), level.Elevation);
                if (!BoundaryValidation.IsValidHorizontalBoundary(loops)) throw new InvalidDataException("Invalid/intersecting floor boundary");
                var floor = Floor.Create(doc, loops, ft.Id, level.Id);
                Set(floor, BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM, Geometry.Feet(p.D("topOffset")));
                result = floor; break;
            case "Door": case "Window": result = Opening(e, level); break;
            case "Column": result=config.AutomaticSimpleColumns?basicColumns.Create(e,level,materials):MappedFamily(e,level);break;
            case "Beam": case "Object": case "Lamp": result = MappedFamily(e, level); break;
            case "Morph": result = Morph(e); break;
            case "Zone":
                var point = geometry.Xyz(p.GetProperty("point").Read<Point>());
                var uv = new UV(point.X, point.Y);
                SpatialElement zone;
                if (config.ZoneMode == "space") zone = doc.Create.NewSpace(level, uv);
                else zone = doc.Create.NewRoom(level, uv);
                zone.Name = p.S("name");
                var number = zone.get_Parameter(BuiltInParameter.ROOM_NUMBER);
                if (number != null && !number.IsReadOnly) number.Set(p.S("number"));
                var offset = zone.get_Parameter(BuiltInParameter.ROOM_LOWER_OFFSET);
                if (offset != null && !offset.IsReadOnly) offset.Set(Geometry.Feet(p.D("baseOffset")));
                doc.Regenerate();
                double area = zone is Room room ? room.Area : ((Space)zone).Area;
                // Source lines and circular arcs are integrated analytically, including holes.
                var sourceLoops = p.GetProperty("loops").Read<List<List<Edge>>>();
                double sourceArea = Math.Abs(Area(sourceLoops[0])) - sourceLoops.Skip(1).Sum(l => Math.Abs(Area(l)));
                double areaM2 = UnitUtils.ConvertFromInternalUnits(area, UnitTypeId.SquareMeters);
                double difference = Math.Abs(areaM2-sourceArea);
                bool accepted = area > 0 && difference <= config.RoomAreaToleranceSquareMeters + 1e-9;
                Report.ZoneAreas.Add(new(e.SourceGuid, sourceArea, areaM2, difference, config.RoomAreaToleranceSquareMeters,
                    accepted, area <= 0 ? "notEnclosed" : accepted ? "withinTolerance" : "overTolerance"));
                if (area <= 0) throw new InvalidDataException("Zone not enclosed by imported hosts; area tolerance does not replace enclosure");
                if (!accepted)
                    throw new InvalidDataException($"Room boundary mismatch: source {sourceArea:G8} m2, Revit {areaM2:G8} m2");
                result = zone; break;
            default: throw new InvalidDataException("No adapter for " + e.Kind);
        }
        var mark = result.get_Parameter(BuiltInParameter.ALL_MODEL_MARK);
        if (mark != null && !mark.IsReadOnly && mark.StorageType == StorageType.String) mark.Set(e.Mark);
        return result;
    }
    static double Area(List<Edge> edges) => SourcePlanarGeometry.SignedArea(edges);
    List<XYZ> ValidateMesh(MeshRecord mesh)
    {
        var vertices = mesh.Vertices.Select(geometry.Xyz).ToList();
        if (vertices.Count < 3 || mesh.Faces.Count == 0) throw new InvalidDataException("Empty mesh");
        foreach (var face in mesh.Faces)
        {
            if (face.Count == 0) throw new InvalidDataException("Empty mesh face");
            XYZ? normal = null; XYZ? origin = null;
            foreach (var loop in face)
            {
                if (loop.Count < 3 || loop.Distinct().Count() != loop.Count || loop.Any(i => i < 0 || i >= vertices.Count))
                    throw new InvalidDataException("Invalid mesh face indices");
                origin ??= vertices[loop[0]];
                for (int i = 1; normal == null && i+1 < loop.Count; ++i)
                {
                    var n = (vertices[loop[i]]-origin).CrossProduct(vertices[loop[i+1]]-origin);
                    if (n.GetLength() > 1e-9) normal = n.Normalize();
                }
                if (normal == null || loop.Any(i => Math.Abs((vertices[i]-origin).DotProduct(normal)) > 1e-5))
                    throw new InvalidDataException("Degenerate/nonplanar mesh face");
            }
        }
        return vertices;
    }
    Element Morph(ElementRecord e)
    {
        var mesh = e.Payload.GetProperty("mesh").Read<MeshRecord>();
        var vertices = ValidateMesh(mesh);
        var key = e.Payload.S("materialKey");
        var material = string.IsNullOrEmpty(key) ? ElementId.InvalidElementId : materials.TryGetValue(key, out var m) ? m.Id : throw new InvalidDataException("Morph building material missing");
        using var builder = new TessellatedShapeBuilder();
        builder.OpenConnectedFaceSet(true);
        foreach (var face in mesh.Faces)
        {
            IList<IList<XYZ>> rings = face.Select(r => (IList<XYZ>)r.Select(i => vertices[i]).ToList()).ToList();
            using var f = new TessellatedFace(rings, material);
            if (!builder.DoesFaceHaveEnoughLoopsAndVertices(f)) throw new InvalidDataException("Revit rejected a source mesh face");
            builder.AddFace(f);
        }
        builder.CloseConnectedFaceSet();
        builder.Target = mesh.Closed ? TessellatedShapeBuilderTarget.Solid : TessellatedShapeBuilderTarget.AnyGeometry;
        builder.Fallback = mesh.Closed ? TessellatedShapeBuilderFallback.Abort : TessellatedShapeBuilderFallback.Mesh;
        builder.Build();
        var built = builder.GetBuildResult().GetGeometricalObjects();
        if (built.Count == 0) throw new InvalidDataException("Revit built no geometry");
        if (mesh.Closed)
        {
            double sourceVolume = 0;
            foreach (var face in mesh.Faces)
                foreach (var ring in face)
                    for (int i = 1; i+1 < ring.Count; ++i)
                        sourceVolume += vertices[ring[0]].DotProduct(vertices[ring[i]].CrossProduct(vertices[ring[i+1]]))/6;
            double actual = built.OfType<Solid>().Sum(s => s.Volume);
            if (built.Any(g => g is not Solid) || Math.Abs(actual-Math.Abs(sourceVolume)) > Math.Max(1e-5, Math.Abs(sourceVolume)*1e-5))
                throw new InvalidDataException("Revit solid volume differs from source faceted body");
        }
        var shape = DirectShape.CreateElement(doc, new ElementId(BuiltInCategory.OST_GenericModel));
        shape.ApplicationId = "Ac2Revit"; shape.ApplicationDataId = project+":"+e.SourceGuid;
        shape.SetShape(built);
        Report.Add(e.SourceGuid, e.Kind, "notice", "Source faceted body imported as DirectShape. Surface overrides/textures remain in source metadata.");
        return shape;
    }
    void Dimension(FamilyInstance instance, string custom, double meters)
    {
        if (string.IsNullOrEmpty(custom)) throw new InvalidDataException("Map an explicit family length parameter");
        var parameter = instance.LookupParameter(custom) ?? instance.Symbol.LookupParameter(custom);
        if (parameter == null || parameter.StorageType != StorageType.Double || parameter.Definition.GetDataType() != SpecTypeId.Length)
            throw new InvalidDataException("Family length parameter missing: " + custom);
        var value = Geometry.Feet(meters);
        if (parameter.Element.Id == instance.Symbol.Id)
        {
            if (Math.Abs(parameter.AsDouble()-value) > 1e-6) throw new InvalidDataException("Family type size differs: " + custom);
        }
        else if (parameter.IsReadOnly || !parameter.Set(value)) throw new InvalidDataException("Family size parameter is read-only: " + custom);
    }
    Element MappedFamily(ElementRecord e, Level level)
    {
        var mapping = Mapping(e); var symbol = Symbol(e, mapping); var d = e.Payload.GetProperty("details");
        if (!symbol.IsActive) { symbol.Activate(); doc.Regenerate(); }
        var offset = mapping.OriginOffset;
        Point Shift(Point p, double angle) => new() { X = p.X + offset.X*Math.Cos(angle)-offset.Y*Math.Sin(angle),
            Y = p.Y + offset.X*Math.Sin(angle)+offset.Y*Math.Cos(angle), Z = p.Z+offset.Z };
        FamilyInstance instance;
        if (e.Kind == "Beam")
        {
            var start = d.GetProperty("begCoordinate").Read<Point>(); var end = d.GetProperty("endCoordinate").Read<Point>();
            start.Z = end.Z = d.D("zCoordinate");
            double angle = Math.Atan2(end.Y-start.Y, end.X-start.X);
            var a = geometry.Xyz(Shift(start, angle)); var b = geometry.Xyz(Shift(end, angle));
            instance = doc.Create.NewFamilyInstance(Line.CreateBound(a, b), symbol, level, StructuralType.Beam);
            Dimension(instance, mapping.WidthParameter, d.D("width"));
            Dimension(instance, mapping.HeightParameter, d.D("height"));
            var rotation = instance.get_Parameter(BuiltInParameter.STRUCTURAL_BEND_DIR_ANGLE);
            if (rotation == null || rotation.IsReadOnly) throw new InvalidDataException("Beam cross section rotation unavailable");
            rotation.Set(d.D("profileAngle")+mapping.RotationOffsetRadians);
            // No joins/snapping are requested: source analytical axis must be preserved.
            doc.Regenerate();
            var actual = ((LocationCurve)instance.Location).Curve;
            if (actual.GetEndPoint(0).DistanceTo(a) > 1e-5 || actual.GetEndPoint(1).DistanceTo(b) > 1e-5)
                throw new InvalidDataException("Revit changed the source beam axis");
        }
        else
        {
            var sourcePoint = d.GetProperty("origin").Read<Point>();
            if (e.Kind == "Column") sourcePoint.Z = d.D("zCoordinate");
            double angle = d.D(e.Kind == "Column" ? "axisRotationAngle" : "angle");
            var location = geometry.Xyz(Shift(sourcePoint, angle));
            instance = doc.Create.NewFamilyInstance(location, symbol, level, e.Kind == "Column" ? StructuralType.Column : StructuralType.NonStructural);
            if (e.Kind == "Column")
            {
                instance.get_Parameter(BuiltInParameter.FAMILY_BASE_LEVEL_PARAM).Set(level.Id);
                instance.get_Parameter(BuiltInParameter.FAMILY_TOP_LEVEL_PARAM).Set(level.Id);
                Set(instance, BuiltInParameter.FAMILY_BASE_LEVEL_OFFSET_PARAM, location.Z-level.Elevation);
                Set(instance, BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM, location.Z-level.Elevation+Geometry.Feet(d.D("height")));
                Dimension(instance, mapping.WidthParameter, d.D("width"));
                Dimension(instance, mapping.DepthParameter, d.D("depth"));
            }
            else
            {
                var dims = d.GetProperty("dimensions").Read<Point>();
                Dimension(instance, mapping.WidthParameter, dims.X);
                Dimension(instance, mapping.DepthParameter, dims.Y);
                Dimension(instance, mapping.HeightParameter, dims.Z);
            }
            double rotation = angle + mapping.RotationOffsetRadians + manifest.Coordinates.Transform.RotationRadians;
            ElementTransformUtils.RotateElement(doc, instance.Id, Line.CreateBound(location, location+XYZ.BasisZ), rotation);
            if (e.Kind is "Object" or "Lamp" && d.B("reflected"))
                ElementTransformUtils.MirrorElement(doc, instance.Id, Plane.CreateByNormalAndOrigin(new XYZ(Math.Cos(rotation), Math.Sin(rotation), 0), location));
        }
        Report.Add(e.SourceGuid, e.Kind, "notice", "Equivalent calibrated Revit family used; original GDL/profile, materials and flags retained as source metadata.");
        return instance;
    }
    Element Opening(ElementRecord e, Level level)
    {
        var p = e.Payload;
        if (hosts[p.S("hostGuid")] is not Wall wall) throw new InvalidDataException("Opening host is not a native wall");
        var mapping = Mapping(e); var symbol = Symbol(e, mapping);
        if (!symbol.IsActive) { symbol.Activate(); doc.Regenerate(); }
        var path = ((LocationCurve)wall.Location).Curve;
        var center = Geometry.Feet(p.D("centerOffset")); var width = Geometry.Feet(p.D("width"));
        if (center-width/2 < -1e-6 || center+width/2 > path.Length+1e-6) throw new InvalidDataException("Opening extends past wall ends");
        // NewRoom/Window placement requires actual host curve; normalized fraction follows arc length.
        var xy = path.Evaluate(center/path.Length, true);
        double baseZ = levels[manifest.Elements.Single(x => x.SourceGuid.Equals(p.S("hostGuid"), StringComparison.OrdinalIgnoreCase)).LevelKey].Elevation
                     + wall.get_Parameter(BuiltInParameter.WALL_BASE_OFFSET).AsDouble();
        var location = new XYZ(xy.X, xy.Y, baseZ+Geometry.Feet(p.D("sillHeight")+mapping.SillFromWallBase));
        var instance = doc.Create.NewFamilyInstance(location, symbol, wall, level, StructuralType.NonStructural);
        void Size(string custom, BuiltInParameter builtin, double meters)
        {
            var param = string.IsNullOrEmpty(custom) ? instance.get_Parameter(builtin) : instance.LookupParameter(custom);
            if (param == null) param = string.IsNullOrEmpty(custom) ? symbol.get_Parameter(builtin) : symbol.LookupParameter(custom);
            if (param == null || param.StorageType != StorageType.Double || param.Definition.GetDataType() != SpecTypeId.Length)
                throw new InvalidDataException("Missing length parameter for opening size");
            double value = Geometry.Feet(meters);
            if (param.Element.Id == symbol.Id)
            {
                if (Math.Abs(param.AsDouble()-value) > 1e-6) throw new InvalidDataException("Mapped type dimensions differ. Map a dedicated type or instance size parameters.");
            }
            else if (param.IsReadOnly || !param.Set(value)) throw new InvalidDataException("Opening size parameter is read-only");
        }
        Size(mapping.WidthParameter, BuiltInParameter.FAMILY_WIDTH_PARAM, p.D("width"));
        Size(mapping.HeightParameter, BuiltInParameter.FAMILY_HEIGHT_PARAM, p.D("height"));
        bool hand = p.B("reflected") ^ mapping.HandFlipXor;
        bool facing = p.B("refSide") ^ mapping.FacingFlipXor;
        if (instance.HandFlipped != hand && !instance.flipHand()) throw new InvalidDataException("Family cannot flip hand");
        if (instance.FacingFlipped != facing && !instance.flipFacing()) throw new InvalidDataException("Family cannot flip facing");
        doc.Regenerate();
        if (instance.Host?.Id != wall.Id) throw new InvalidDataException("Opening host mismatch");
        Report.Add(e.SourceGuid, e.Kind, "notice", "Family orientation uses a calibrated mapping. Archicad oSide retained in source metadata.");
        return instance;
    }
    sealed class Failures(Report report, string key, string kind) : IFailuresPreprocessor
    {
        public FailureProcessingResult PreprocessFailures(FailuresAccessor accessor)
        {
            bool error = false;
            foreach (var failure in accessor.GetFailureMessages())
            {
                report.Add(key, kind, failure.GetSeverity()==FailureSeverity.Warning?"warning":"revitError", failure.GetDescriptionText());
                if (failure.GetSeverity() == FailureSeverity.Warning) accessor.DeleteWarning(failure);
                else error = true;
            }
            return error ? FailureProcessingResult.ProceedWithRollBack : FailureProcessingResult.Continue;
        }
    }
}
