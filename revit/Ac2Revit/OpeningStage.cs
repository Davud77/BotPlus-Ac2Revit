using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Structure;
using System.Text.Json;

namespace Ac2Revit;

/// <summary>Native wall voids plus separately verified IFC door/window filling geometry.</summary>
public static class OpeningStage
{
    public static Report Run(Document doc,string plan,string template,string folder,int limit=0,Action<string,int,int>? progress=null,ISet<string>? kinds=null)
    {
        using var json=JsonDocument.Parse(File.ReadAllText(plan));var root=json.RootElement;string project=root.S("projectId");
        var coordinate=root.GetProperty("coordinates").Read<CoordinateInfo>().Transform;
        if(coordinate.RotationRadians!=0 || coordinate.Translation.X!=0 || coordinate.Translation.Y!=0 || coordinate.Translation.Z!=0)throw new InvalidDataException("Opening stage requires checked identity coordinates");
        VerifiedReferenceScope.Verify(root.S("ifcPath"),root.S("ifcSha256"));
        Directory.CreateDirectory(folder);var report=new Report{ProjectId=project,Mode="hostedOpenings"};var index=Storage.Index(doc,project);
        var records=root.GetProperty("elements").EnumerateArray().Where(r=>kinds==null || kinds.Contains(r.S("kind"))).ToList();
        if(limit>0)
        {
            var sample=records.Where(r=>r.S("status") is "ready" or "fillingOnly").GroupBy(r=>r.S("status")=="fillingOnly"?"fillingOnly:"+r.S("kind"):(r.B("emptyOpening")?"Empty":r.S("kind"))+":"+r.GetProperty("opening").S("mode"))
                .Select(g=>g.First()).ToList();
            records=sample.Concat(records.Where(r=>r.S("status") is "ready" or "fillingOnly").Where(r=>!sample.Any(s=>s.S("sourceGuid")==r.S("sourceGuid")))).Take(limit).ToList();
        }
        var families=new Dictionary<string,Family>();var accepted=new List<JsonElement>();int done=0,mutations=0;
        using var process=System.Diagnostics.Process.GetCurrentProcess();process.Refresh();long initialMemory=process.PrivateMemorySize64;
        foreach(var record in records)
        {
            string key=record.S("sourceGuid"),cutKey=record.B("emptyOpening")?key:"opening:"+key;
            progress?.Invoke("openingCuts",done++,records.Count);
            if(done%50==0)
            {
                report.Save(Path.Combine(folder,"cut-report.json"));if(!string.IsNullOrEmpty(doc.PathName))doc.Save();
                process.Refresh();
                if(mutations>0 && !string.IsNullOrEmpty(doc.PathName) && process.PrivateMemorySize64-initialMemory>1536L*1024*1024)
                    throw new ImportCheckpointException("Проёмы сохранены; требуется сброс истории и кэша геометрии Revit.");
            }
            if(record.S("status")=="fillingOnly")
            {
                if(record.B("emptyOpening") || record.S("kind") is not ("Door" or "Window") || !record.TryGetProperty("vertices",out _))
                    throw new InvalidDataException("Invalid filling-only record: "+key);
                accepted.Add(record);report.Add(key,record.S("kind"),"incomplete","НЕПОЛНОЦЕННОЕ ЗАПОЛНЕНИЕ: точная IFC-геометрия; вырез не создаётся без подтверждённой связи. "+record.S("reason"));continue;
            }
            if(record.S("status")!="ready"){report.Add(key,record.S("kind"),"blocked",record.S("reason"));continue;}
            if(index.TryGetValue(cutKey,out var retained))
            {
                if(Storage.Read(retained)?.Hash!="opening-ifc:"+root.S("ifcSha256"))throw new InvalidDataException("Existing opening reference changed; update adapter required: "+key);
                accepted.Add(record);report.Add(key,"Opening","unchanged","Existing owned cut retained",retained.UniqueId);continue;
            }
            try
            {
                var hostElement=index.GetValueOrDefault(record.S("hostGuid"))??throw new InvalidDataException("Source opening host is missing");
                if(hostElement is DirectShape)throw new InvalidDataException("IFC net host retained as DirectShape; independent native void cut is not supported");
                var raw=record.GetProperty("rawSource");var opening=record.GetProperty("opening");
                var vg=record.GetProperty("voidGeometry");
                var points=vg.GetProperty("vertices").Read<List<Point>>().Select(NativeGeometry.At).ToList();
                var center=new XYZ((points.Min(p=>p.X)+points.Max(p=>p.X))/2,(points.Min(p=>p.Y)+points.Max(p=>p.Y))/2,(points.Min(p=>p.Z)+points.Max(p=>p.Z))/2);
                var local=Transform.CreateTranslation(center.Negate());var source=NativeGeometry.MeshSolids(vg,local);
                var world=source.Select(s=>SolidUtils.CreateTransformed(s,Transform.CreateTranslation(center))).ToList();
                var before=NativeGeometry.Solids(hostElement);if(before.Count==0)throw new InvalidDataException("Opening host has no solid geometry");
                var expected=before.Select(s=>{var remainder=s;foreach(var cut in world)remainder=BooleanOperationsUtils.ExecuteBooleanOperation(remainder,cut,BooleanOperationsType.Difference);return remainder;}).Where(s=>s.Volume>1e-12).ToList();
                double reduction=UnitUtils.ConvertFromInternalUnits(before.Sum(s=>s.Volume)-expected.Sum(s=>s.Volume),UnitTypeId.CubicMeters);
                if(reduction<=1e-7)throw new InvalidDataException("IFC void does not intersect the actual native host; no guessed repositioning");
                Family? cutFamily=null;
                if(opening.S("mode")!="rectangle")
                {
                    string shapeKey=Json.Hash(new{vertices=points.Select(p=>new[]{Math.Round(UnitUtils.ConvertFromInternalUnits(p.X-center.X,UnitTypeId.Meters),6),Math.Round(UnitUtils.ConvertFromInternalUnits(p.Y-center.Y,UnitTypeId.Meters),6),Math.Round(UnitUtils.ConvertFromInternalUnits(p.Z-center.Z,UnitTypeId.Meters),6)}),faces=vg.GetProperty("faces")});
                    if(!families.TryGetValue(shapeKey,out cutFamily))
                    {
                        string familyKey="opening-void-family:"+shapeKey;
                        if(index.GetValueOrDefault(familyKey) is Family existing)cutFamily=existing;
                        else
                        {
                            string file=Path.Combine(folder,"AC2R_OpeningVoid_"+shapeKey[..24]+".rfa");
                            CutBeamReplacer.BuildVoid(doc.Application,template,file,source,report,key);
                            using var load=new Transaction(doc,"Load exact source opening void");load.Start();
                            if(!doc.LoadFamily(file,out cutFamily) || cutFamily==null)throw new InvalidDataException("Could not load opening void family");
                            Storage.Tag(cutFamily,project,familyKey,root.S("ifcSha256"),"");load.Commit();index[familyKey]=cutFamily;
                        }
                        families[shapeKey]=cutFamily;
                    }
                }
                using var t=new Transaction(doc,"Source wall opening "+key);t.Start();NativeGeometry.Configure(t,report,key,"Opening");
                Element cutElement;
                if(cutFamily==null)
                {
                    if(hostElement is not Wall wall)throw new InvalidDataException("Rectangular wall opening requires a native Wall host");
                    cutElement=doc.Create.NewOpening(wall,NativeGeometry.At(opening.GetProperty("sourceStart").Read<Point>()),NativeGeometry.At(opening.GetProperty("sourceEnd").Read<Point>()));
                }
                else
                {
                    var symbol=(FamilySymbol)doc.GetElement(cutFamily.GetFamilySymbolIds().First());if(!symbol.IsActive){symbol.Activate();doc.Regenerate();}
                    var cutter=doc.Create.NewFamilyInstance(center,symbol,StructuralType.NonStructural);doc.Regenerate();
                    if(!InstanceVoidCutUtils.CanBeCutWithVoid(hostElement))throw new InvalidDataException("Native host cannot be cut with an instance void: "+hostElement.GetType().Name);
                    InstanceVoidCutUtils.AddInstanceVoidCut(doc,hostElement,cutter);cutElement=cutter;
                }
                doc.Regenerate();NativeGeometry.VerifySolids(NativeGeometry.Solids(hostElement),expected);
                Storage.Tag(cutElement,project,cutKey,"opening-ifc:"+root.S("ifcSha256"),record.GetRawText());
                cutElement.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)?.Set("Source host "+record.S("hostGuid")+"; exact IFC void; filling "+key);
                if(t.Commit()!=TransactionStatus.Committed)throw new InvalidDataException("Opening cut rolled back");
                index[cutKey]=cutElement;accepted.Add(record);mutations++;
                report.Add(key,"Opening","created",$"Native {cutElement.GetType().Name}; removed {reduction:G9} m3. Host shape checked against before minus exact IFC void in both Boolean directions.",cutElement.UniqueId);
            }
            catch(Exception ex)
            {
                report.Add(key,"Opening","blocked",ex.ToString());
                // A failed native host cut does not invalidate the independently
                // identified IFC filling. Keep its exact geometry with a visible
                // incomplete-transfer note instead of silently losing the door/window.
                if(!record.B("emptyOpening") && record.TryGetProperty("vertices",out _) && !accepted.Any(r=>r.S("sourceGuid")==key))
                {accepted.Add(record);report.Add(key,record.S("kind"),"incomplete","Точная IFC-геометрия заполнения будет перенесена; нативный проём не подтверждён.");}
            }
        }
        report.Save(Path.Combine(folder,"cut-report.json"));
        foreach(string kind in new[]{"Door","Window"})
        {
            var selected=accepted.Where(r=>r.S("kind")==kind && !r.B("emptyOpening") && !index.ContainsKey(r.S("sourceGuid"))).ToList();
            if(selected.Count==0)continue;
            string body=Path.Combine(folder,"fillings-"+kind+".json");
            using(var stream=File.Create(body))
            using(var writer=new Utf8JsonWriter(stream))
            {
                writer.WriteStartObject();
                foreach(string name in new[]{"projectId","coordinates","ifcPath","ifcSha256"}){writer.WritePropertyName(name);root.GetProperty(name).WriteTo(writer);}
                writer.WritePropertyName("elements");writer.WriteStartArray();
                foreach(var record in selected)
                {
                    writer.WriteStartObject();foreach(var property in record.EnumerateObject())if(property.Name!="reason")property.WriteTo(writer);
                    bool nativeCut=index.ContainsKey("opening:"+record.S("sourceGuid"));
                    string note=nativeCut?"Exact IFC filling geometry; native wall cut is stored separately":"НЕПОЛНОЦЕННОЕ ЗАПОЛНЕНИЕ — точная геометрия IFC; нативный проём не подтверждён. Проверьте хост и вырез.";
                    if(record.S("classificationAdaptation").Length>0)note+="; "+record.S("classificationAdaptation");
                    writer.WriteString("reason",note);writer.WriteEndObject();
                }
                writer.WriteEndArray();writer.WriteEndObject();
            }
            var bodyReport=ReferenceFallback.Run(doc,body,Path.GetFullPath,(n,total)=>
            {progress?.Invoke("filling"+kind,n,total);if(n%50==0 && !string.IsNullOrEmpty(doc.PathName))doc.Save();},kind=="Door"?BuiltInCategory.OST_Doors:BuiltInCategory.OST_Windows,kind);
            report.Items.AddRange(bodyReport.Items);
        }
        return report;
    }
}
