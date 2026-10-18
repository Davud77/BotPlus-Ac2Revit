using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Structure;
using System.Text.Json;
using System.Security.Cryptography;

namespace Ac2Revit;

/// <summary>Replace only verified imported meshes with level-driven native extrusions in a copied RVT.</summary>
public static class ExtrudedColumnReplacer
{
    public sealed class Part
    {
        public List<List<Point>> Loops {get;set;}=[];
        public double BottomOffset {get;set;}
        public double TopOffset {get;set;}
        public double AreaM2 {get;set;}
        public string MaterialKey {get;set;}="";
    }
    public sealed class Column
    {
        public string SourceGuid {get;set;}="";
        public string FamilyKey {get;set;}="";
        public string LevelKey {get;set;}="";
        public string Mark {get;set;}="";
        public Point Origin {get;set;}=new();
        public double Angle {get;set;}
        public double Height {get;set;}
        public double VolumeM3 {get;set;}
        public double[] BoundsMinMeters {get;set;}=[];
        public double[] BoundsMaxMeters {get;set;}=[];
        public List<Part> Parts {get;set;}=[];
        public JsonElement RawSource {get;set;}
    }
    public static Report Run(Document doc,string planPath,string template,string folder,Action<string,int,int>? progress=null)
    {
        using var json=JsonDocument.Parse(File.ReadAllText(planPath));var root=json.RootElement;
        string project=root.GetProperty("projectId").GetString()!;
        var transform=root.GetProperty("coordinates").Read<CoordinateInfo>().Transform;
        if(transform.RotationRadians!=0 || transform.Translation.X!=0 || transform.Translation.Y!=0 || transform.Translation.Z!=0)
            throw new InvalidDataException("Profile replacement currently requires the checked identity coordinate transform");
        using(var stream=File.OpenRead(root.GetProperty("ifcPath").GetString()!))
            if(!Convert.ToHexString(SHA256.HashData(stream)).Equals(root.S("ifcSha256"),StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("Reference IFC changed");
        var columns=root.GetProperty("columns").Read<List<Column>>();
        if(columns.Count!=columns.Select(c=>c.SourceGuid).Distinct(StringComparer.OrdinalIgnoreCase).Count())throw new InvalidDataException("Duplicate column GUID in plan");
        var index=Storage.Index(doc,project);var report=new Report{ProjectId=project,Mode="nativeExtrudedColumnsCopy"};
        Directory.CreateDirectory(folder);int done=0;
        using var process=System.Diagnostics.Process.GetCurrentProcess();process.Refresh();long initialMemory=process.PrivateMemorySize64;
        string? memoryLimit=null;
        foreach(var group in columns.GroupBy(c=>c.FamilyKey))
        {
            process.Refresh();
            if(process.PrivateMemorySize64>4L*1024*1024*1024 || process.PrivateMemorySize64-initialMemory>512L*1024*1024)
                memoryLimit="Остановлена нативная замена: достигнут бюджет памяти; точная IFC-колонна сохранена.";
            if(memoryLimit!=null)
            {foreach(var column in group)report.Add(column.SourceGuid,"Column","retained",memoryLimit,index.GetValueOrDefault(column.SourceGuid)?.UniqueId??"");continue;}
            FamilySymbol? symbol=null;string familyError="";
            try
            {
                progress?.Invoke("profileFamily",done,columns.Count);
                string file=BuildFamily(doc.Application,template,folder,group.First(),report);
                using var t=new Transaction(doc,"Load generated source profile");t.Start();
                if(!doc.LoadFamily(file,out var family) || family==null)throw new InvalidDataException("Could not load isolated generated family");
                if(family.FamilyPlacementType!=FamilyPlacementType.TwoLevelsBased || family.FamilyCategory.Id!=new ElementId(BuiltInCategory.OST_StructuralColumns))throw new InvalidDataException("Generated family is not a two-level structural column");
                symbol=(FamilySymbol)doc.GetElement(family.GetFamilySymbolIds().First());
                Storage.Tag(family,project,"profile-family:"+group.Key,group.Key,"");
                if(t.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Family load rolled back");
                report.Add(group.Key,"ColumnProfileFamily","created",file,family.UniqueId);
            }
            catch(Exception ex){familyError=ex.ToString();report.Add(group.Key,"ColumnProfileFamily","blocked",familyError);}
            bool flexChecked=false;
            foreach(var column in group)
            {
                progress?.Invoke("replaceColumns",done++,columns.Count);
                process.Refresh();
                if(process.PrivateMemorySize64>4L*1024*1024*1024 || process.PrivateMemorySize64-initialMemory>512L*1024*1024)memoryLimit="Остановлена нативная замена: достигнут бюджет памяти; точная IFC-колонна сохранена.";
                if(memoryLimit!=null){report.Add(column.SourceGuid,"Column","retained",memoryLimit,index.GetValueOrDefault(column.SourceGuid)?.UniqueId??"");continue;}
                if(symbol==null){report.Add(column.SourceGuid,"Column","retained","Family generation failed: "+familyError);continue;}
                using var t=new Transaction(doc,"Native source profile column");
                try
                {
                    if(index.GetValueOrDefault(column.SourceGuid) is not DirectShape old || old.Category.Id!=new ElementId(BuiltInCategory.OST_StructuralColumns))
                        throw new InvalidDataException("Replacement requires exactly one tagged source DirectShape column");
                    if(index.GetValueOrDefault(column.LevelKey) is not Level level)throw new InvalidDataException("Source level missing");
                    t.Start();var options=t.GetFailureHandlingOptions();options.SetFailuresPreprocessor(new Failures(report,column.SourceGuid));options.SetClearAfterRollback(true);t.SetFailureHandlingOptions(options);
                    if(!symbol.IsActive){symbol.Activate();doc.Regenerate();}
                    var origin=At(column.Origin);
                    var instance=doc.Create.NewFamilyInstance(origin,symbol,level,StructuralType.Column);
                    SetId(instance,BuiltInParameter.FAMILY_BASE_LEVEL_PARAM,level.Id);SetId(instance,BuiltInParameter.FAMILY_TOP_LEVEL_PARAM,level.Id);
                    Set(instance,BuiltInParameter.FAMILY_BASE_LEVEL_OFFSET_PARAM,origin.Z-level.Elevation);
                    double top=origin.Z-level.Elevation+Geometry.Feet(column.Height);
                    Set(instance,BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM,top);
                    ElementTransformUtils.RotateElement(doc,instance.Id,Line.CreateBound(origin,origin+XYZ.BasisZ),column.Angle);
                    for(int i=0;i<column.Parts.Count;i++)
                        if(!string.IsNullOrEmpty(column.Parts[i].MaterialKey) && index.GetValueOrDefault("material:"+column.Parts[i].MaterialKey.ToUpperInvariant()) is Material material)
                            instance.LookupParameter("AC2R_Material_"+i)?.Set(material.Id);
                    doc.Regenerate();Verify(instance,column,0);
                    if(!flexChecked)
                    {
                        using var flex=new SubTransaction(doc);flex.Start();
                        Set(instance,BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM,top+Geometry.Feet(0.5));doc.Regenerate();Verify(instance,column,0.5);flex.RollBack();
                        doc.Regenerate();Verify(instance,column,0);
                        report.Add(column.SourceGuid,"ColumnFlex","verified","Top offset +0.5 m changes actual solid height and volume; original geometry restored.");
                    }
                    instance.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)?.Set(column.Mark);
                    var oldId=old.Id;
                    var dependentElements=old.GetDependentElements(null).Select(id=>doc.GetElement(id)).Where(e=>e!=null).ToList();
                    var dependents=dependentElements.ToDictionary(e=>e.Id.Value,e=>e.GetType().Name);
                    // Revit may delete its uncategorised read-only bookkeeping
                    // element along with a mesh. Never allow tags, dimensions,
                    // other model elements, DataStorage or writable user data.
                    var bookkeeping=dependentElements.Where(e=>e.GetType()==typeof(Element) && e.Category==null && string.IsNullOrEmpty(e.Name)
                        && !e.ViewSpecific && Storage.Read(e)==null && e.Parameters.Cast<Parameter>().All(p=>p.IsReadOnly) && e.get_BoundingBox(null)==null)
                        .Select(e=>e.Id.Value).ToHashSet();
                    var deleted=doc.Delete(oldId);
                    if(!deleted.Contains(oldId) || deleted.Any(id=>id!=oldId && !bookkeeping.Contains(id.Value)))throw new InvalidDataException("Replacing the mesh would delete dependents: "+string.Join(", ",deleted.Select(id=>$"{id.Value}:{dependents.GetValueOrDefault(id.Value,"source/unknown")}")));
                    if(deleted.Count>1)report.Add(column.SourceGuid,"ColumnBookkeeping","notice","Revit removed only uncategorised read-only bookkeeping dependencies: "+string.Join(",",deleted.Where(id=>id!=oldId).Select(id=>id.Value)));
                    Storage.Tag(instance,project,column.SourceGuid,"extruded-v1:"+Json.Hash(column),column.RawSource.GetRawText());
                    if(t.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Native column transaction rolled back");
                    index[column.SourceGuid]=instance;flexChecked=true;
                    report.Add(column.SourceGuid,"Column","replaced","Native two-level FamilyInstance; exact polygon extrusion with holes/parts; bounds and volume checked.",instance.UniqueId);
                }
                catch(Exception ex){if(t.GetStatus()==TransactionStatus.Started)t.RollBack();report.Add(column.SourceGuid,"Column","retained",ex.ToString());}
                if(done%25==0 && !string.IsNullOrEmpty(doc.PathName)){report.Save(Path.Combine(folder,"replacement-checkpoint.json"));doc.Save();}
            }
        }
        return report;
    }
    static XYZ At(Point p)=>new(Geometry.Feet(p.X),Geometry.Feet(p.Y),Geometry.Feet(p.Z));
    static void SetId(Element e,BuiltInParameter id,ElementId value){var p=e.get_Parameter(id);if(p==null || p.IsReadOnly || !p.Set(value))throw new InvalidDataException("Constraint unavailable: "+id);}
    static void Set(Element e,BuiltInParameter id,double value){var p=e.get_Parameter(id);if(p==null || p.IsReadOnly || !p.Set(value))throw new InvalidDataException("Length unavailable: "+id);}
    static string BuildFamily(Autodesk.Revit.ApplicationServices.Application app,string template,string folder,Column source,Report report)
    {
        string path=Path.Combine(folder,"AC2R_Profile_"+source.FamilyKey[..20]+".rfa");
        if(File.Exists(path))throw new IOException("Generated family output already exists");
        var family=app.NewFamilyDocument(template);
        try
        {
            using(var t=new Transaction(family,"Native source polygon extrusions"))
            {
                t.Start();var failureOptions=t.GetFailureHandlingOptions();failureOptions.SetFailuresPreprocessor(new Failures(report,"profile-family:"+source.FamilyKey));failureOptions.SetClearAfterRollback(true);t.SetFailureHandlingOptions(failureOptions);
                var fm=family.FamilyManager;if(fm.CurrentType==null)fm.NewType("Source profile");
                var levels=new FilteredElementCollector(family).OfClass(typeof(Level)).Cast<Level>().OrderBy(l=>l.Elevation).ToList();
                if(levels.Count<2)throw new InvalidDataException("Structural column template requires lower and upper reference levels");
                var bottom=levels.First();var top=levels.Last();
                top.Elevation=bottom.Elevation+Geometry.Feet(Math.Max(3,source.Parts.Max(p=>p.BottomOffset-p.TopOffset)+1));
                var view=new FilteredElementCollector(family).OfClass(typeof(ViewSection)).Cast<ViewSection>().First(v=>!v.IsTemplate && Math.Abs(v.ViewDirection.Z)<0.01);
                var offsets=new Dictionary<string,Reference>();
                var anchors=new Dictionary<ElementId,Reference>();
                Reference Anchor(Level level)
                {
                    if(anchors.TryGetValue(level.Id,out var cached))return cached;
                    var right=view.RightDirection;
                    var plane=family.FamilyCreate.NewReferencePlane(right*-Geometry.Feet(2)+XYZ.BasisZ*level.Elevation,right*Geometry.Feet(2)+XYZ.BasisZ*level.Elevation,view.ViewDirection,view);
                    family.Regenerate();family.FamilyCreate.NewAlignment(view,level.GetPlaneReference(),plane.GetReference());
                    anchors[level.Id]=plane.GetReference();return anchors[level.Id];
                }
                Reference Datum(Level level,double offset)
                {
                    if(Math.Abs(offset)<1e-7)return level.GetPlaneReference();
                    string key=level.Id.Value+":"+offset.ToString("R",System.Globalization.CultureInfo.InvariantCulture);
                    if(offsets.TryGetValue(key,out var existing))return existing;
                    double z=level.Elevation+Geometry.Feet(offset);var right=view.RightDirection;
                    var plane=family.FamilyCreate.NewReferencePlane(right*-Geometry.Feet(2)+XYZ.BasisZ*z,right*Geometry.Feet(2)+XYZ.BasisZ*z,view.ViewDirection,view);
                    var anchor=Anchor(level);family.Regenerate();
                    var refs=new ReferenceArray();refs.Append(anchor);refs.Append(plane.GetReference());
                    var line=Line.CreateBound(right*-Geometry.Feet(3)+XYZ.BasisZ*(Math.Min(z,level.Elevation)-Geometry.Feet(1)),right*-Geometry.Feet(3)+XYZ.BasisZ*(Math.Max(z,level.Elevation)+Geometry.Feet(1)));
                    var dim=family.FamilyCreate.NewDimension(view,line,refs);dim.IsLocked=true;offsets[key]=plane.GetReference();return offsets[key];
                }
                for(int i=0;i<source.Parts.Count;i++)
                {
                    var p=source.Parts[i];double z0=bottom.Elevation+Geometry.Feet(p.BottomOffset),z1=top.Elevation+Geometry.Feet(p.TopOffset);
                    if(z1-z0<app.ShortCurveTolerance)throw new InvalidDataException("Profile part has invalid reference height");
                    var rings=new CurveArrArray();
                    foreach(var ring in p.Loops)
                    {
                        var curves=new CurveArray();
                        for(int j=0;j<ring.Count;j++)
                        {
                            var a=At(ring[j])+XYZ.BasisZ*z0;var b=At(ring[(j+1)%ring.Count])+XYZ.BasisZ*z0;
                            if(a.DistanceTo(b)<app.ShortCurveTolerance)throw new InvalidDataException("Exact profile contains a Revit-short edge; no automatic simplification");
                            curves.Append(Line.CreateBound(a,b));
                        }
                        rings.Append(curves);
                    }
                    var sketch=SketchPlane.Create(family,Plane.CreateByNormalAndOrigin(XYZ.BasisZ,XYZ.BasisZ*z0));
                    var extrusion=family.FamilyCreate.NewExtrusion(true,rings,sketch,z1-z0);
                    var material=fm.AddParameter("AC2R_Material_"+i,GroupTypeId.Materials,SpecTypeId.Reference.Material,true);
                    fm.AssociateElementParameterToFamilyParameter(extrusion.get_Parameter(BuiltInParameter.MATERIAL_ID_PARAM),material);
                    family.Regenerate();
                    Reference Cap(double z)
                    {
                        var geo=extrusion.get_Geometry(new Options{ComputeReferences=true});
                        return geo.OfType<Solid>().SelectMany(s=>s.Faces.Cast<Face>()).OfType<PlanarFace>()
                            .First(f=>Math.Abs(Math.Abs(f.FaceNormal.Z)-1)<1e-7 && Math.Abs(f.Origin.Z-z)<Geometry.Feet(1e-6)).Reference;
                    }
                    var lower=Cap(z0);var upper=Cap(z1);
                    family.FamilyCreate.NewAlignment(view,Datum(bottom,p.BottomOffset),lower);
                    family.FamilyCreate.NewAlignment(view,Datum(top,p.TopOffset),upper);
                    family.Regenerate();
                }
                if(t.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Family generation rolled back");
            }
            family.SaveAs(path,new SaveAsOptions{OverwriteExistingFile=false});return path;
        }
        finally {family.Close(false);}
    }
    static void Verify(FamilyInstance instance,Column reference,double extraHeight)
    {
        var points=new List<XYZ>();double volume=0;
        void Read(GeometryElement geometry)
        {
            foreach(var item in geometry)
                if(item is GeometryInstance g)Read(g.GetInstanceGeometry());
                else if(item is Solid solid && solid.Volume>0){volume+=UnitUtils.ConvertFromInternalUnits(solid.Volume,UnitTypeId.CubicMeters);foreach(Face face in solid.Faces)points.AddRange(face.Triangulate().Vertices);}
                else if(item is Mesh)throw new InvalidDataException("Native profile contains a mesh instead of solids");
        }
        Read(instance.get_Geometry(new Options{DetailLevel=ViewDetailLevel.Fine}));
        if(points.Count==0)throw new InvalidDataException("Native column has no solid geometry");
        var lo=new XYZ(points.Min(p=>p.X),points.Min(p=>p.Y),points.Min(p=>p.Z));var hi=new XYZ(points.Max(p=>p.X),points.Max(p=>p.Y),points.Max(p=>p.Z));
        var min=reference.BoundsMinMeters;var max=reference.BoundsMaxMeters;
        if(lo.DistanceTo(At(new Point{X=min[0],Y=min[1],Z=min[2]}))>Geometry.Feet(.001) || hi.DistanceTo(At(new Point{X=max[0],Y=max[1],Z=max[2]+extraHeight}))>Geometry.Feet(.001))
            throw new InvalidDataException("Native profile bounds or height flex differ from source");
        double expected=reference.VolumeM3+extraHeight*reference.Parts.Sum(p=>p.AreaM2);
        if(Math.Abs(volume-expected)>Math.Max(1e-6,expected*1e-5))throw new InvalidDataException($"Native profile volume/flex mismatch: {volume:G9} vs {expected:G9} m3");
    }
    sealed class Failures(Report report,string key):IFailuresPreprocessor
    {
        public FailureProcessingResult PreprocessFailures(FailuresAccessor accessor)
        {
            bool error=false;foreach(var failure in accessor.GetFailureMessages())
            {report.Add(key,"Column",failure.GetSeverity()==FailureSeverity.Warning?"warning":"revitError",failure.GetDescriptionText());if(failure.GetSeverity()==FailureSeverity.Warning)accessor.DeleteWarning(failure);else error=true;}
            return error?FailureProcessingResult.ProceedWithRollBack:FailureProcessingResult.Continue;
        }
    }
}
