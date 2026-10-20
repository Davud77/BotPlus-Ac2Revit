using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Structure;
using System.Text.Json;

namespace Ac2Revit;

/// <summary>Opt-in replacement of IFC-proven uncut horizontal rectangular beams only.</summary>
public static class RectangularBeamReplacer
{
    public sealed class Beam
    {
        public string SourceGuid {get;set;}="";
        public string LevelKey {get;set;}="";
        public string Mark {get;set;}="";
        public string MaterialKey {get;set;}="";
        public Point Start {get;set;}=new();
        public Point End {get;set;}=new();
        public double Width {get;set;}
        public double Height {get;set;}
        public double VolumeM3 {get;set;}
        public double[] BoundsMinMeters {get;set;}=[];
        public double[] BoundsMaxMeters {get;set;}=[];
        public JsonElement RawSource {get;set;}
    }
    static XYZ At(Point p)=>new(Geometry.Feet(p.X),Geometry.Feet(p.Y),Geometry.Feet(p.Z));
    static void Set(Element e,BuiltInParameter id,double value)
    {
        var p=e.get_Parameter(id);
        if(p==null || p.IsReadOnly || !p.Set(value))throw new InvalidDataException("Beam length parameter unavailable: "+id);
    }
    static void Integer(Element e,BuiltInParameter id,int value)
    {
        var p=e.get_Parameter(id);
        if(p==null || p.IsReadOnly || !p.Set(value))throw new InvalidDataException("Beam justification unavailable: "+id);
    }
    static Parameter Dimension(FamilySymbol symbol,BuiltInParameter id,string fallback)
    {
        var p=symbol.get_Parameter(id) ?? symbol.LookupParameter(fallback);
        if(p==null || p.IsReadOnly || p.StorageType!=StorageType.Double || p.Definition.GetDataType()!=SpecTypeId.Length)
            throw new InvalidDataException("Expected beam section type parameter: "+fallback+"; available: "+string.Join(",",symbol.Parameters.Cast<Parameter>().Select(p=>p.Definition.Name)));
        return p;
    }
    public static Report Run(Document doc,string plan,string familyPath,Action<int,int>? progress=null)
    {
        using var json=JsonDocument.Parse(File.ReadAllText(plan));var root=json.RootElement;
        string project=root.S("projectId");var coordinate=root.GetProperty("coordinates").Read<CoordinateInfo>().Transform;
        if(coordinate.RotationRadians!=0 || coordinate.Translation.X!=0 || coordinate.Translation.Y!=0 || coordinate.Translation.Z!=0)
            throw new InvalidDataException("Beam calibration currently requires identity coordinates");
        VerifiedReferenceScope.Verify(root.S("ifcPath"),root.S("ifcSha256"));
        var beams=root.GetProperty("beams").Read<List<Beam>>();
        if(beams.Count!=beams.Select(b=>b.SourceGuid).Distinct(StringComparer.OrdinalIgnoreCase).Count())throw new InvalidDataException("Duplicate beam source GUID");
        var index=Storage.Index(doc,project);var report=new Report{ProjectId=project,Mode="nativeRectangularBeamsCopy"};
        FamilySymbol first;
        using(var t=new Transaction(doc,"Load isolated rectangular beam family"))
        {
            t.Start();
            if(!doc.LoadFamily(familyPath,out var family) || family==null)throw new InvalidDataException("Could not load isolated beam family");
            if(family.FamilyCategory.Id!=new ElementId(BuiltInCategory.OST_StructuralFraming) || family.FamilyPlacementType!=FamilyPlacementType.CurveDrivenStructural)
                throw new InvalidDataException("Expected CurveDrivenStructural framing family");
            first=(FamilySymbol)doc.GetElement(family.GetFamilySymbolIds().First());
            Storage.Tag(family,project,"family:auto-rectangular-beam",Json.Hash(File.ReadAllBytes(familyPath)),"");
            if(t.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Beam family load rolled back");
        }
        var types=new Dictionary<string,FamilySymbol>();int done=0;
        foreach(var beam in beams)
        {
            using var t=new Transaction(doc,"Native calibrated rectangular beam");
            try
            {
                if(index.GetValueOrDefault(beam.SourceGuid) is not DirectShape old || old.Category.Id!=new ElementId(BuiltInCategory.OST_StructuralFraming))
                    throw new InvalidDataException("Requires one tagged source beam DirectShape");
                if(index.GetValueOrDefault(beam.LevelKey) is not Level level)throw new InvalidDataException("Source home level missing");
                var d=beam.RawSource.GetProperty("details");
                if(d.B("isSlanted") || d.S("anchorPoint")!="TopCenter" || d.D("arcAngle")!=0 || d.D("offset")!=0)
                    throw new InvalidDataException("Plan exceeds supported rectangular beam cases");
                t.Start();var options=t.GetFailureHandlingOptions();options.SetFailuresPreprocessor(new Failures(report,beam.SourceGuid));options.SetClearAfterRollback(true);t.SetFailureHandlingOptions(options);
                string typeKey=Json.Hash(new{beam.Width,beam.Height,beam.MaterialKey});
                bool newType=!types.TryGetValue(typeKey,out var symbol);
                if(newType)
                {
                    symbol=(FamilySymbol)first.Duplicate("AC2R "+typeKey[..16]);
                    Dimension(symbol,BuiltInParameter.STRUCTURAL_SECTION_COMMON_WIDTH,"b").Set(Geometry.Feet(beam.Width));
                    Dimension(symbol,BuiltInParameter.STRUCTURAL_SECTION_COMMON_HEIGHT,"h").Set(Geometry.Feet(beam.Height));
                }
                if(!symbol!.IsActive){symbol.Activate();doc.Regenerate();}
                var a=At(beam.Start);var b=At(beam.End);
                var instance=doc.Create.NewFamilyInstance(Line.CreateBound(a,b),symbol,level,StructuralType.Beam);
                doc.Regenerate();
                // Framing may associate its reference with the nearest level,
                // while Element.LevelId remains InvalidElementId. Set the actual
                // framing reference explicitly and restore the world curve.
                var referenceLevel=instance.get_Parameter(BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM);
                if(referenceLevel==null || referenceLevel.IsReadOnly || !referenceLevel.Set(level.Id))
                    throw new InvalidDataException("Cannot set beam reference level; "+string.Join("; ",instance.Parameters.Cast<Parameter>().Where(p=>p.StorageType==StorageType.ElementId).Select(p=>$"{p.Id.Value}:{p.Definition.Name}={p.AsElementId().Value},readonly={p.IsReadOnly}")));
                doc.Regenerate();((LocationCurve)instance.Location).Curve=Line.CreateBound(a,b);doc.Regenerate();
                StructuralFramingUtils.DisallowJoinAtEnd(instance,0);StructuralFramingUtils.DisallowJoinAtEnd(instance,1);
                Integer(instance,BuiltInParameter.YZ_JUSTIFICATION,(int)YZJustificationOption.Uniform);
                Integer(instance,BuiltInParameter.Y_JUSTIFICATION,(int)YJustification.Center);
                Integer(instance,BuiltInParameter.Z_JUSTIFICATION,(int)ZJustification.Top);
                Set(instance,BuiltInParameter.Y_OFFSET_VALUE,0);Set(instance,BuiltInParameter.Z_OFFSET_VALUE,0);
                Set(instance,BuiltInParameter.START_EXTENSION,0);Set(instance,BuiltInParameter.END_EXTENSION,0);
                Set(instance,BuiltInParameter.STRUCTURAL_BEND_DIR_ANGLE,0);
                // NewFamilyInstance(curve, symbol, level, Beam) establishes the
                // level and the world curve. End elevations can be computed
                // read-only parameters, so do not overwrite derived values.
                doc.Regenerate();
                if(referenceLevel.AsElementId()!=level.Id)throw new InvalidDataException("Beam framing reference differs from source home story");
                if(index.GetValueOrDefault("material:"+beam.MaterialKey.ToUpperInvariant()) is not Material material)throw new InvalidDataException("Source building material missing");
                var mp=instance.get_Parameter(BuiltInParameter.STRUCTURAL_MATERIAL_PARAM);
                if(mp==null || mp.IsReadOnly || !mp.Set(material.Id))throw new InvalidDataException("Beam structural material unavailable");
                doc.Regenerate();Verify(instance,beam,0);
                // Exercise actual curve-driven family geometry before accepting a type.
                if(newType)
                {
                    using var flex=new SubTransaction(doc);flex.Start();
                    ((LocationCurve)instance.Location).Curve=Line.CreateBound(a,b+(b-a).Normalize()*Geometry.Feet(.5));
                    doc.Regenerate();Verify(instance,beam,.5);flex.RollBack();doc.Regenerate();Verify(instance,beam,0);
                    report.Add(beam.SourceGuid,"BeamFlex","verified","Actual curve extension +0.5 m verified in bounds and volume, then rolled back.");
                }
                instance.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)?.Set(beam.Mark);
                var oldId=old.Id;
                var bookkeeping=old.GetDependentElements(null).Select(id=>doc.GetElement(id)).Where(e=>e!=null && e.GetType()==typeof(Element) && e.Category==null && string.IsNullOrEmpty(e.Name)
                    && !e.ViewSpecific && Storage.Read(e)==null && e.Parameters.Cast<Parameter>().All(p=>p.IsReadOnly) && e.get_BoundingBox(null)==null).Select(e=>e.Id).ToHashSet();
                var deleted=doc.Delete(oldId);
                if(!deleted.Contains(oldId) || deleted.Any(id=>id!=oldId && !bookkeeping.Contains(id)))throw new InvalidDataException("Mesh deletion would affect user dependents");
                Storage.Tag(instance,project,beam.SourceGuid,"rect-beam-v1:"+Json.Hash(beam),beam.RawSource.GetRawText());
                if(t.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Native beam transaction rolled back");
                types[typeKey]=symbol;index[beam.SourceGuid]=instance;
                report.Add(beam.SourceGuid,"Beam","replaced","Native structural beam: source TopCenter, level and offsets; source material. Complete IFC prism preflight; actual solid bounds, volume and axis checked.",instance.UniqueId);
            }
            catch(Exception ex){if(t.GetStatus()==TransactionStatus.Started)t.RollBack();report.Add(beam.SourceGuid,"Beam","retained",ex.ToString());}
            progress?.Invoke(++done,beams.Count);
        }
        return report;
    }
    static void Verify(FamilyInstance instance,Beam beam,double extension)
    {
        var a=At(beam.Start);var b=At(beam.End);var axis=(b-a).Normalize();b+=axis*Geometry.Feet(extension);
        var curve=((LocationCurve)instance.Location).Curve;
        if(curve.GetEndPoint(0).DistanceTo(a)>Geometry.Feet(1e-6) || curve.GetEndPoint(1).DistanceTo(b)>Geometry.Feet(1e-6))throw new InvalidDataException("Beam axis moved");
        if(StructuralFramingUtils.IsJoinAllowedAtEnd(instance,0) || StructuralFramingUtils.IsJoinAllowedAtEnd(instance,1))throw new InvalidDataException("Beam automatic end trim enabled");
        var vertices=new List<XYZ>();double volume=0;
        void Read(GeometryElement g)
        {
            foreach(var o in g)
                if(o is GeometryInstance i)Read(i.GetInstanceGeometry());
                else if(o is Solid s && s.Volume>0){volume+=s.Volume;foreach(Face f in s.Faces)vertices.AddRange(f.Triangulate().Vertices);}
                else if(o is Mesh)throw new InvalidDataException("Native beam contains mesh");
        }
        Read(instance.get_Geometry(new Options{DetailLevel=ViewDetailLevel.Fine}));
        if(vertices.Count==0)throw new InvalidDataException("Native beam solid missing");
        double length=(b-a).GetLength(),width=Geometry.Feet(beam.Width),height=Geometry.Feet(beam.Height);
        var y=XYZ.BasisZ.CrossProduct(axis);
        var local=vertices.Select(v=>v-a).Select(v=>new XYZ(v.DotProduct(axis),v.DotProduct(y),v.Z)).ToList();
        var lo=new XYZ(local.Min(p=>p.X),local.Min(p=>p.Y),local.Min(p=>p.Z));var hi=new XYZ(local.Max(p=>p.X),local.Max(p=>p.Y),local.Max(p=>p.Z));
        if(lo.DistanceTo(new XYZ(0,-width/2,-height))>Geometry.Feet(.001) || hi.DistanceTo(new XYZ(length,width/2,0))>Geometry.Feet(.001))
            throw new InvalidDataException($"Native beam local bounds differ: {lo} / {hi}; expected length/width/height {length}/{width}/{height}");
        double expected=beam.VolumeM3+beam.Width*beam.Height*extension;
        double actual=UnitUtils.ConvertFromInternalUnits(volume,UnitTypeId.CubicMeters);
        if(Math.Abs(actual-expected)>Math.Max(1e-6,expected*1e-5))throw new InvalidDataException("Native beam volume differs");
    }
    sealed class Failures(Report report,string key):IFailuresPreprocessor
    {
        public FailureProcessingResult PreprocessFailures(FailuresAccessor accessor)
        {
            bool error=false;
            foreach(var f in accessor.GetFailureMessages())
            {
                report.Add(key,"Beam",f.GetSeverity()==FailureSeverity.Warning?"warning":"revitError",f.GetDescriptionText());
                if(f.GetSeverity()==FailureSeverity.Warning)accessor.DeleteWarning(f);else error=true;
            }
            return error?FailureProcessingResult.ProceedWithRollBack:FailureProcessingResult.Continue;
        }
    }
}
