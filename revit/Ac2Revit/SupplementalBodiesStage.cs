using Autodesk.Revit.DB;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Ac2Revit;

/// <summary>Exact IFC geometry for categories with no native adapter, in bounded batches.</summary>
public static class SupplementalBodiesStage
{
    public static Report Run(Document doc,string planPath,string folder,Action<string,int,int>? progress=null)
    {
        string Resolve(string path)=>Path.GetFullPath(path,Path.GetDirectoryName(planPath)!);
        var bundle=JsonNode.Parse(File.ReadAllText(planPath))!;
        if(bundle["format"]?.GetValue<string>()!="ac2r.supplemental/1")throw new InvalidDataException("Invalid supplemental body plan");
        string manifestPath=Resolve(bundle["sourceManifest"]!.GetValue<string>());
        var manifest=Manifest.Load(manifestPath);string project=manifest.Source.ProjectId;
        if(bundle["projectId"]!.GetValue<string>()!=project)throw new InvalidDataException("Supplemental project mismatch");
        string manifestSha,frameSha,referenceSha=bundle["ifcSha256"]!.GetValue<string>();
        using(var stream=File.OpenRead(manifestPath))manifestSha=Convert.ToHexString(SHA256.HashData(stream));
        string framePath=Resolve(bundle["ifcCoordinateFrame"]!.GetValue<string>());
        using(var stream=File.OpenRead(framePath))frameSha=Convert.ToHexString(SHA256.HashData(stream));
        using var frame=JsonDocument.Parse(File.ReadAllText(framePath));string frameHash=frame.RootElement.S("hash");
        var guard=Storage.Index(doc,project).GetValueOrDefault("pipeline-source-snapshot");
        if(guard==null || Storage.Read(guard)?.Hash!=Json.Hash(new{manifestSha,referenceSha,frameSha}))throw new InvalidDataException("Supplemental source/frame does not match the saved core model");
        Directory.CreateDirectory(folder);var report=new Report{ProjectId=project,Mode="supplementalIfcBodies"};int batchNumber=0;
        foreach(var batch in bundle["batches"]!.AsArray())
        {
            string bodyPath=Resolve(batch!["path"]!.GetValue<string>()),sourceKind=batch["kind"]!.GetValue<string>();
            string auditPath=Path.Combine(Path.GetDirectoryName(bodyPath)!,"audit.json");
            if(File.Exists(auditPath))
            {
                using var audit=JsonDocument.Parse(File.ReadAllText(auditPath));
                foreach(var row in audit.RootElement.GetProperty(sourceKind.ToLowerInvariant()+"s").EnumerateArray())
                    if(row.S("status")=="error")report.Add(row.S("sourceGuid"),"Supplemental:"+sourceKind,"blocked",row.S("error"));
            }
            var data=JsonNode.Parse(File.ReadAllText(bodyPath))!;
            if(data["ifcToSource"]?["hash"]?.GetValue<string>()!=frameHash || data["ifcSha256"]?.GetValue<string>()!=referenceSha)throw new InvalidDataException("Supplemental IFC frame/hash mismatch");
            var index=Storage.Index(doc,project);
            var missing=data["elements"]!.AsArray().Where(r=>r!=null && !index.ContainsKey(r["sourceGuid"]!.GetValue<string>())).Select(r=>r!.DeepClone()).ToArray();
            data["elements"]=new JsonArray(missing);
            string input=Path.Combine(folder,$"{batchNumber++:0000}.json");File.WriteAllText(input,data.ToJsonString());
            if(missing.Length>0)
            {
                string kind=sourceKind;
                var stage=ReferenceFallback.Run(doc,input,Resolve,(done,total)=>progress?.Invoke("supplemental:"+kind,done,total),BuiltInCategory.OST_GenericModel,"Supplemental:"+kind,uniqueMarks:true);
                report.Items.AddRange(stage.Items);report.Save(Path.Combine(folder,"checkpoint-report.json"));
                if(!string.IsNullOrEmpty(doc.PathName))doc.Save();
            }
            using var process=System.Diagnostics.Process.GetCurrentProcess();process.Refresh();
            if(missing.Length>0 && process.PrivateMemorySize64>8L*1024*1024*1024)
                throw new ImportCheckpointException("Дополнительная IFC-геометрия сохранена; требуется сброс истории и кэша Revit.");
        }
        return report;
    }
}
