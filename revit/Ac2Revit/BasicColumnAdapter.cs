using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Structure;

namespace Ac2Revit;

/// <summary>Opt-in native rectangular columns; every source must have a checked IFC prism.</summary>
public sealed class BasicColumnAdapter(Document doc, ImportConfig config, Geometry geometry, RigidTransform transform, string project, Report report)
{
    readonly Dictionary<string,FamilySymbol> types = new();
    static string Section(ElementRecord e) => Json.Hash(new{width=e.Payload.GetProperty("details").D("width"),depth=e.Payload.GetProperty("details").D("depth")});
    public void Validate(ElementRecord e)
    {
        var p=e.Payload;var d=p.GetProperty("details");
        if(e.Status=="unsupported" || d.TryGetProperty("profileId",out _) || d.B("isSlanted") || d.B("circleBased") || d.B("wrapping") || d.S("coreAnchor")!="Center")
            throw new InvalidDataException("Automatic column adapter supports only centred unwrapped vertical rectangular sections");
        if(!p.TryGetProperty("columnReference",out var reference) || reference.D("volumeM3")<=0)
            throw new InvalidDataException("IFC prism calibration is required for automatic columns");
        if(new[]{d.D("width"),d.D("depth"),d.D("height")}.Any(v=>!double.IsFinite(v)||v<=0))throw new InvalidDataException("Invalid column dimensions");
    }
    public void Prepare(IEnumerable<ElementRecord> columns)
    {
        var selected=columns.ToList();if(selected.Count==0)return;
        if(!File.Exists(config.ColumnFamilyFile))throw new FileNotFoundException("Column family file missing",config.ColumnFamilyFile);
        string root=Path.GetFullPath(Path.Combine(Path.GetDirectoryName(typeof(App).Assembly.Location)!,"..",".."))+Path.DirectorySeparatorChar;
        if(!Path.GetFullPath(config.ColumnFamilyFile).StartsWith(root,StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("Generated column family input must be inside workspace");
        using var t=new Transaction(doc,"Prepare calibrated rectangular column types");t.Start();
        Family? family;
        string familyKey="family:auto-rectangular-column";
        family=Storage.Index(doc,project).GetValueOrDefault(familyKey) as Family;
        if(family==null)
        {
            if(!doc.LoadFamily(config.ColumnFamilyFile,out family) || family==null)throw new InvalidOperationException("Could not load isolated column family; a same-name family may already exist");
            Storage.Tag(family,project,familyKey,Json.Hash(File.ReadAllBytes(config.ColumnFamilyFile)),"");
        }
        if(family.FamilyPlacementType!=FamilyPlacementType.TwoLevelsBased || family.FamilyCategory.Id!=new ElementId(BuiltInCategory.OST_StructuralColumns))
            throw new InvalidDataException("Isolated family must be a native TwoLevelsBased structural column");
        var first=(FamilySymbol)doc.GetElement(family.GetFamilySymbolIds().First());
        report.Add(familyKey,"ColumnFamily","notice",string.Join("; ",first.Parameters.Cast<Parameter>().Where(p=>p.StorageType==StorageType.Double && p.Definition.GetDataType()==SpecTypeId.Length).Select(p=>p.Definition.Name)));
        foreach(var e in selected.GroupBy(Section).Select(g=>g.First()))
        {
            Validate(e);var d=e.Payload.GetProperty("details");string name="AC2R "+Section(e)[..16];
            var symbol=family.GetFamilySymbolIds().Select(id=>(FamilySymbol)doc.GetElement(id)).SingleOrDefault(s=>s.Name==name) ?? (FamilySymbol)first.Duplicate(name);
            void Size(string parameterName,double meters)
            {
                var parameter=symbol.LookupParameter(parameterName);
                if(parameter==null || parameter.IsReadOnly || parameter.StorageType!=StorageType.Double || parameter.Definition.GetDataType()!=SpecTypeId.Length || !parameter.Set(Geometry.Feet(meters)))
                    throw new InvalidDataException("Column type length parameter unavailable: "+parameterName);
            }
            Size(config.ColumnWidthParameter,d.D("width"));Size(config.ColumnDepthParameter,d.D("depth"));types[Section(e)]=symbol;
        }
        doc.Regenerate();if(t.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Column type preparation rolled back");
    }
    public Element Create(ElementRecord e,Level level,Dictionary<string,Material> materials)
    {
        Validate(e);var d=e.Payload.GetProperty("details");var symbol=types[Section(e)];
        if(!symbol.IsActive){symbol.Activate();doc.Regenerate();}
        var source=d.GetProperty("origin").Read<Point>();source.Z=d.D("zCoordinate");var position=geometry.Xyz(source);
        var instance=doc.Create.NewFamilyInstance(position,symbol,level,StructuralType.Column);
        void Id(BuiltInParameter id,ElementId value){var p=instance.get_Parameter(id);if(p==null || p.IsReadOnly || !p.Set(value))throw new InvalidDataException("Column constraint unavailable: "+id);}
        void Length(BuiltInParameter id,double value){var p=instance.get_Parameter(id);if(p==null || p.IsReadOnly || !p.Set(value))throw new InvalidDataException("Column offset unavailable: "+id);}
        Id(BuiltInParameter.FAMILY_BASE_LEVEL_PARAM,level.Id);Id(BuiltInParameter.FAMILY_TOP_LEVEL_PARAM,level.Id);
        Length(BuiltInParameter.FAMILY_BASE_LEVEL_OFFSET_PARAM,position.Z-level.Elevation);
        Length(BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM,position.Z-level.Elevation+Geometry.Feet(d.D("height")));
        ElementTransformUtils.RotateElement(doc,instance.Id,Line.CreateBound(position,position+XYZ.BasisZ),d.D("axisRotationAngle")+transform.RotationRadians);
        string materialKey=d.GetProperty("buildingMaterialId").S("guid").ToUpperInvariant();
        if(!materials.TryGetValue(materialKey,out var material))throw new InvalidDataException("Column building material missing");
        var mp=instance.get_Parameter(BuiltInParameter.STRUCTURAL_MATERIAL_PARAM);
        if(mp==null || mp.IsReadOnly || !mp.Set(material.Id))throw new InvalidDataException("Column structural material unavailable");
        doc.Regenerate();CheckGeometry(instance,e);
        report.Add(e.SourceGuid,"Column","geometryVerified","Native family solid bounds and volume match the prechecked IFC rectangular prism; this adapter excludes profiles/cuts/slants.",instance.UniqueId);
        return instance;
    }
    void CheckGeometry(FamilyInstance instance,ElementRecord e)
    {
        var points=new List<XYZ>();double volume=0;
        void Read(GeometryElement g)
        {
            foreach(var item in g)
                if(item is GeometryInstance i)Read(i.GetInstanceGeometry());
                else if(item is Solid s && s.Volume>0){volume+=s.Volume;foreach(Face f in s.Faces){var mesh=f.Triangulate();points.AddRange(mesh.Vertices);}}
                else if(item is Mesh)throw new InvalidDataException("Native column unexpectedly contains mesh geometry");
        }
        Read(instance.get_Geometry(new Options{DetailLevel=ViewDetailLevel.Fine}));
        if(points.Count==0)throw new InvalidDataException("Native column has no solid geometry");
        var reference=e.Payload.GetProperty("columnReference");var lo=reference.GetProperty("boundsMinMeters").Read<double[]>();var hi=reference.GetProperty("boundsMaxMeters").Read<double[]>();
        // Convert the IFC bounds only for identity/translation transforms. Rotated
        // world AABBs cannot be transformed as two corners without enlarging them.
        if(Math.Abs(transform.RotationRadians)>1e-10)throw new InvalidDataException("Rotated IFC column audit requires transformed reference vertices");
        var min=geometry.Xyz(new Point{X=lo[0],Y=lo[1],Z=lo[2]});var max=geometry.Xyz(new Point{X=hi[0],Y=hi[1],Z=hi[2]});
        var actualLo=new XYZ(points.Min(p=>p.X),points.Min(p=>p.Y),points.Min(p=>p.Z));var actualHi=new XYZ(points.Max(p=>p.X),points.Max(p=>p.Y),points.Max(p=>p.Z));
        if(Math.Max(actualLo.DistanceTo(min),actualHi.DistanceTo(max))>Geometry.Feet(0.001))throw new InvalidDataException("Native column bounds do not match IFC within 1 mm");
        double volumeM3=UnitUtils.ConvertFromInternalUnits(volume,UnitTypeId.CubicMeters),expected=reference.D("volumeM3");
        if(Math.Abs(volumeM3-expected)>Math.Max(1e-6,expected*1e-5))throw new InvalidDataException("Native column solid volume differs from IFC prism");
    }
}
