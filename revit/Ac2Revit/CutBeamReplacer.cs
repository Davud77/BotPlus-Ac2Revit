using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Structure;
using System.Text.Json;
using System.Security.Cryptography;

namespace Ac2Revit;

/// <summary>Native rectangular framing cut by exact Boolean voids; retain the mesh on any failed proof.</summary>
public static class CutBeamReplacer
{
    static void Number(Element e,BuiltInParameter id,double value)
    {var p=e.get_Parameter(id);if(p==null || p.IsReadOnly || !p.Set(value))throw new InvalidDataException("Beam parameter unavailable: "+id);}
    static void Choice(Element e,BuiltInParameter id,int value)
    {var p=e.get_Parameter(id);if(p==null || p.IsReadOnly || !p.Set(value))throw new InvalidDataException("Beam option unavailable: "+id);}
    static FamilyInstance Create(Document doc,FamilySymbol symbol,Level level,XYZ a,XYZ b,Material material,double startExtension,double endExtension)
    {
        if(!symbol.IsActive){symbol.Activate();doc.Regenerate();}
        var instance=doc.Create.NewFamilyInstance(Line.CreateBound(a,b),symbol,level,StructuralType.Beam);doc.Regenerate();
        var rp=instance.get_Parameter(BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM);
        if(rp==null || rp.IsReadOnly || !rp.Set(level.Id))throw new InvalidDataException("Cannot set source beam reference level");
        doc.Regenerate();((LocationCurve)instance.Location).Curve=Line.CreateBound(a,b);
        StructuralFramingUtils.DisallowJoinAtEnd(instance,0);StructuralFramingUtils.DisallowJoinAtEnd(instance,1);
        Choice(instance,BuiltInParameter.YZ_JUSTIFICATION,(int)YZJustificationOption.Uniform);
        Choice(instance,BuiltInParameter.Y_JUSTIFICATION,(int)YJustification.Center);
        Choice(instance,BuiltInParameter.Z_JUSTIFICATION,(int)ZJustification.Top);
        Number(instance,BuiltInParameter.Y_OFFSET_VALUE,0);Number(instance,BuiltInParameter.Z_OFFSET_VALUE,0);
        Number(instance,BuiltInParameter.STRUCTURAL_BEND_DIR_ANGLE,0);
        Number(instance,BuiltInParameter.START_EXTENSION,startExtension);Number(instance,BuiltInParameter.END_EXTENSION,endExtension);
        instance.get_Parameter(BuiltInParameter.STRUCTURAL_MATERIAL_PARAM).Set(material.Id);doc.Regenerate();return instance;
    }
    internal static string BuildVoid(Autodesk.Revit.ApplicationServices.Application app,string template,string file,List<Solid> cuts,Report report,string key)
    {
        if(File.Exists(file))throw new IOException("Void family output exists");
        var family=app.NewFamilyDocument(template);
        try
        {
            using(var t=new Transaction(family,"Exact native beam voids"))
            {
                t.Start();NativeGeometry.Configure(t,report,key,"BeamVoidFamily");
                if(family.FamilyManager.CurrentType==null)family.FamilyManager.NewType("Source cuts");
                var permission=family.OwnerFamily.get_Parameter(BuiltInParameter.FAMILY_ALLOW_CUT_WITH_VOIDS);
                if(permission==null || !permission.Set(1))throw new InvalidDataException("Template does not allow unattached void cuts");
                family.OwnerFamily.get_Parameter(BuiltInParameter.FAMILY_ALWAYS_VERTICAL)?.Set(0);
                foreach(var solid in cuts)
                {
                    var form=FreeFormElement.Create(family,solid);
                    var cutting=form.get_Parameter(BuiltInParameter.ELEMENT_IS_CUTTING);
                    if(cutting==null || !cutting.Set(1))throw new InvalidDataException("FreeForm cannot become a native void");
                }
                family.Regenerate();if(t.Commit()!=TransactionStatus.Committed)throw new InvalidDataException("Void family generation rolled back");
            }
            family.SaveAs(file,new SaveAsOptions{OverwriteExistingFile=false});return file;
        }
        finally {family.Close(false);}
    }
    public static Report Run(Document doc,string meshPath,string beamFamilyFile,string voidTemplate,string folder,int limit=0,Action<int,int>? progress=null)
    {
        using var json=JsonDocument.Parse(File.ReadAllText(meshPath));var root=json.RootElement;string project=root.S("projectId");
        var coordinate=root.GetProperty("coordinates").Read<CoordinateInfo>().Transform;
        if(coordinate.RotationRadians!=0 || coordinate.Translation.X!=0 || coordinate.Translation.Y!=0 || coordinate.Translation.Z!=0)throw new InvalidDataException("Cut adapter currently requires identity coordinates");
        using(var stream=File.OpenRead(root.S("ifcPath")))
            if(!Convert.ToHexString(SHA256.HashData(stream)).Equals(root.S("ifcSha256"),StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("IFC changed");
        var report=new Report{ProjectId=project,Mode="nativeCutBeams"};var index=Storage.Index(doc,project);Directory.CreateDirectory(folder);
        Family family;
        if(index.GetValueOrDefault("family:auto-rectangular-beam") is Family existingFamily)family=existingFamily;
        else
        {
            using var load=new Transaction(doc,"Load native beam family");load.Start();
            if(!doc.LoadFamily(beamFamilyFile,out family) || family==null)throw new InvalidDataException("Native beam family missing");
            Storage.Tag(family,project,"family:auto-rectangular-beam",Json.Hash(File.ReadAllBytes(beamFamilyFile)),"");load.Commit();
        }
        var first=(FamilySymbol)doc.GetElement(family.GetFamilySymbolIds().First());
        var records=root.GetProperty("elements").EnumerateArray().Where(r=>index.GetValueOrDefault(r.S("sourceGuid")) is DirectShape).ToList();
        if(limit>0)records=records.Where(r=>r.GetProperty("parts").EnumerateArray().All(p=>p.B("closedEdgeCheck"))).Take(limit).ToList();
        int done=0;
        foreach(var record in records)
        {
            string key=record.S("sourceGuid");var raw=record.GetProperty("rawSource");var d=raw.GetProperty("details");
            try
            {
                if(!record.GetProperty("parts").EnumerateArray().All(p=>p.B("closedEdgeCheck")))throw new InvalidDataException("Open IFC mesh retained; no automatic stitching");
                if(d.S("anchorPoint")!="TopCenter" || d.D("offset")!=0 || d.D("arcAngle")!=0 || d.D("verticalCurveHeight")!=0 || Math.Abs(Math.IEEERemainder(d.D("profileAngle"),2*Math.PI))>1e-8)
                    throw new InvalidDataException("Beam reference/curvature requires another adapter");
                if(index.GetValueOrDefault(record.S("levelKey")) is not Level level)throw new InvalidDataException("Home story missing");
                if(index.GetValueOrDefault("material:"+record.S("materialKey").ToUpperInvariant()) is not Material material)throw new InvalidDataException("Beam material missing");
                var begin=d.GetProperty("begCoordinate").Read<Point>();var end=d.GetProperty("endCoordinate").Read<Point>();
                begin.Z=d.D("zCoordinate");end.Z=begin.Z+Math.Sqrt(Math.Pow(end.X-begin.X,2)+Math.Pow(end.Y-begin.Y,2))*Math.Tan(d.D("slantAngle"));
                var a=NativeGeometry.At(begin);var b=NativeGeometry.At(end);var x=(b-a).Normalize();var y=XYZ.BasisZ.CrossProduct(x).Normalize();var z=x.CrossProduct(y);
                var localToWorld=Transform.Identity;localToWorld.Origin=a;localToWorld.BasisX=x;localToWorld.BasisY=y;localToWorld.BasisZ=z;
                var expected=NativeGeometry.MeshSolids(record,localToWorld.Inverse);
                var sourcePoints=record.GetProperty("vertices").Read<List<Point>>().Select(p=>localToWorld.Inverse.OfPoint(NativeGeometry.At(p))).ToList();
                double startExtension=Math.Max(0,-sourcePoints.Min(p=>p.X)),endExtension=Math.Max(0,sourcePoints.Max(p=>p.X)-a.DistanceTo(b));
                FamilySymbol symbol;string typeName="AC2R Cut "+Json.Hash(new{width=d.D("width"),height=d.D("height")})[..16];
                using(var t=new Transaction(doc,"Prepare native cut-beam section"))
                {
                    t.Start();symbol=family.GetFamilySymbolIds().Select(id=>(FamilySymbol)doc.GetElement(id)).FirstOrDefault(s=>s.Name==typeName) ?? (FamilySymbol)first.Duplicate(typeName);
                    var wp=symbol.get_Parameter(BuiltInParameter.STRUCTURAL_SECTION_COMMON_WIDTH) ?? symbol.LookupParameter("b");
                    var hp=symbol.get_Parameter(BuiltInParameter.STRUCTURAL_SECTION_COMMON_HEIGHT) ?? symbol.LookupParameter("h");
                    if(wp==null || hp==null || !wp.Set(Geometry.Feet(d.D("width"))) || !hp.Set(Geometry.Feet(d.D("height"))))throw new InvalidDataException("Beam section parameters missing");
                    t.Commit();
                }
                List<Solid> nominal;
                using(var pilot=new Transaction(doc,"Native beam trial before exact cuts"))
                {
                    pilot.Start();NativeGeometry.Configure(pilot,report,key,"BeamCut");
                    var instance=Create(doc,symbol,level,a,b,material,startExtension,endExtension);
                    nominal=NativeGeometry.Solids(instance).Select(s=>SolidUtils.CreateTransformed(s,localToWorld.Inverse)).ToList();
                    if(NativeGeometry.DifferenceVolume(expected,nominal)>1e-6)throw new InvalidDataException("IFC body extends outside the nominal native section");
                    pilot.RollBack();
                }
                var cuts=new List<Solid>();
                foreach(var s in nominal)
                {
                    var cut=s;foreach(var target in expected)cut=BooleanOperationsUtils.ExecuteBooleanOperation(cut,target,BooleanOperationsType.Difference);
                    if(cut.Volume>1e-10)cuts.AddRange(SolidUtils.SplitVolumes(cut));
                }
                Family? voidFamily=null;
                if(cuts.Count>0)
                {
                    string file=Path.Combine(folder,"AC2R_BeamCuts_"+key.Replace("-","")+".rfa");
                    BuildVoid(doc.Application,voidTemplate,file,cuts,report,key);
                    using var load=new Transaction(doc,"Load exact beam voids");load.Start();
                    if(!doc.LoadFamily(file,out voidFamily) || voidFamily==null)throw new InvalidDataException("Cannot load generated void family");
                    Storage.Tag(voidFamily,project,"beamcut-family:"+key,root.S("ifcSha256"),"");load.Commit();
                }
                using(var t=new Transaction(doc,"Replace mesh with natively cut beam"))
                {
                    t.Start();NativeGeometry.Configure(t,report,key,"BeamCut");
                    var instance=Create(doc,symbol,level,a,b,material,startExtension,endExtension);
                    FamilyInstance? cutter=null;
                    if(voidFamily!=null)
                    {
                        var vs=(FamilySymbol)doc.GetElement(voidFamily.GetFamilySymbolIds().First());if(!vs.IsActive){vs.Activate();doc.Regenerate();}
                        cutter=doc.Create.NewFamilyInstance(a,vs,StructuralType.NonStructural);
                        double yaw=Math.Atan2(x.Y,x.X);ElementTransformUtils.RotateElement(doc,cutter.Id,Line.CreateBound(a,a+XYZ.BasisZ),yaw);
                        double pitch=-Math.Atan2(x.Z,Math.Sqrt(x.X*x.X+x.Y*x.Y));
                        if(Math.Abs(pitch)>1e-10)ElementTransformUtils.RotateElement(doc,cutter.Id,Line.CreateBound(a,a+y),pitch);
                        doc.Regenerate();
                        if(!InstanceVoidCutUtils.IsVoidInstanceCuttingElement(cutter))throw new InvalidDataException("Generated instance has no unattached cutting void");
                        InstanceVoidCutUtils.AddInstanceVoidCut(doc,instance,cutter);doc.Regenerate();
                        Storage.Tag(cutter,project,"beamcut:"+key,root.S("ifcSha256"),raw.GetRawText());
                    }
                    var actual=NativeGeometry.Solids(instance).Select(s=>SolidUtils.CreateTransformed(s,localToWorld.Inverse)).ToList();
                    NativeGeometry.VerifySolids(actual,expected);
                    var curve=((LocationCurve)instance.Location).Curve;
                    if(curve.GetEndPoint(0).DistanceTo(a)>Geometry.Feet(1e-6) || curve.GetEndPoint(1).DistanceTo(b)>Geometry.Feet(1e-6))throw new InvalidDataException("Cut beam axis moved");
                    NativeGeometry.DeleteMeshOnly(doc,(DirectShape)index[key]);
                    instance.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)?.Set(record.S("mark"));
                    Storage.Tag(instance,project,key,"native-cut-beam:"+Json.Hash(record),raw.GetRawText());
                    if(t.Commit()!=TransactionStatus.Committed)throw new InvalidDataException("Native beam cut rolled back");index[key]=instance;
                    report.Add(key,"Beam","replaced",$"Native framing with {cuts.Count} exact void solids; both Boolean difference directions <= 1e-6 m3. Fixed source cuts require regeneration after source changes.",instance.UniqueId);
                }
            }
            catch(Exception ex){report.Add(key,"Beam","retained",ex.ToString());}
            progress?.Invoke(++done,records.Count);
        }
        return report;
    }
}
