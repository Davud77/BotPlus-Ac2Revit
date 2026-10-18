using Autodesk.Revit.DB;
using Autodesk.Revit.DB.ExtensibleStorage;
using System.Text.Json;

namespace Ac2Revit;

/// <summary>Commit and save spatial evaluation in bounded, resumable batches.</summary>
public static class SpatialStage
{
    sealed class State
    {
        public int Next {get;set;}
        public Report Report {get;set;}=new();
    }
    public static Report Run(Document doc,Manifest source,ImportConfig config,string folder,Action<string,int,int>? progress)
    {
        Directory.CreateDirectory(folder);string project=source.Source.ProjectId,key="pipeline-spatial-checkpoint-v1";
        var ordered=source.OrderedElements().Where(e=>e.Kind=="Zone").ToArray();
        var index=Storage.Index(doc,project);
        var sourceSnapshot=index.GetValueOrDefault("pipeline-source-snapshot");
        string signature=Json.Hash(new{snapshot=sourceSnapshot==null?"":Storage.Read(sourceSnapshot)?.Hash,config,
            zones=ordered.Select(e=>new{e.SourceGuid,e.ContentHash}),
            walls=source.Elements.Where(e=>e.Kind=="Wall").Select(e=>new{e.SourceGuid,id=index.GetValueOrDefault(e.SourceGuid)?.UniqueId})});
        var storage=index.GetValueOrDefault(key) as DataStorage;State state;
        if(storage!=null)
        {
            if(Storage.Read(storage)?.Hash!=signature)throw new InvalidDataException("Spatial checkpoint source/configuration changed; explicit refresh required");
            var schema=Storage.Schema();state=JsonSerializer.Deserialize<State>(storage.GetEntity(schema).Get<string>(schema.GetField("RawJson")),Json.Options)??throw new InvalidDataException("Invalid spatial checkpoint");
            if(state.Next<0 || state.Next>ordered.Length)throw new InvalidDataException("Invalid spatial checkpoint cursor");
        }
        else
        {
            state=new(){Report=new(){ProjectId=project,Mode="checkpointedRooms"}};
            using var reset=new Transaction(doc,"Reset owned spatial elements once");reset.Start();
            foreach(var record in ordered)
                if(index.GetValueOrDefault(record.SourceGuid) is SpatialElement spatial)
                {
                    var allowed=spatial.GetDependentElements(null).Select(id=>doc.GetElement(id)).Where(e=>e!=null && e.GetType()==typeof(Element)
                        && e.Category==null && string.IsNullOrEmpty(e.Name) && !e.ViewSpecific && Storage.Read(e)==null
                        && e.Parameters.Cast<Parameter>().All(p=>p.IsReadOnly) && e.get_BoundingBox(null)==null).Select(e=>e.Id).ToHashSet();
                    var spatialId=spatial.Id;var removed=doc.Delete(spatialId);
                    if(removed.Any(id=>id!=spatialId && !allowed.Contains(id)))throw new InvalidDataException("Refreshing room would remove dependent user data: "+record.SourceGuid);
                }
            storage=DataStorage.Create(doc);Storage.Tag(storage,project,key,signature,JsonSerializer.Serialize(state,Json.Options));reset.Commit();
            if(!string.IsNullOrEmpty(doc.PathName))doc.Save();
        }
        using var process=System.Diagnostics.Process.GetCurrentProcess();process.Refresh();long initialMemory=process.PrivateMemorySize64;int batches=0;
        var original=source.Elements;
        try
        {
            while(state.Next<ordered.Length)
            {
                int start=state.Next;var batch=ordered.Skip(start).Take(100).ToHashSet();
                source.Elements=original.Where(e=>e.Kind!="Zone" || batch.Contains(e)).ToList();
                var result=new Importer(doc,source,config){Progress=(_,n,_)=>progress?.Invoke("zoneAreas",start+n,ordered.Length)}
                    .Run(new HashSet<string>{"Zone"},false,false);
                state.Report.Items.AddRange(result.Items);state.Report.ZoneAreas.AddRange(result.ZoneAreas);state.Next+=batch.Count;
                using(var transaction=new Transaction(doc,"Record spatial checkpoint"))
                {transaction.Start();Storage.Tag(storage,project,key,signature,JsonSerializer.Serialize(state,Json.Options));transaction.Commit();}
                if(!string.IsNullOrEmpty(doc.PathName))doc.Save();
                state.Report.Save(Path.Combine(folder,"room-checkpoint-report.json"));progress?.Invoke("zoneAreas",state.Next,ordered.Length);
                process.Refresh();batches++;
                if(state.Next<ordered.Length && !string.IsNullOrEmpty(doc.PathName) && (batches>=4 || process.PrivateMemorySize64-initialMemory>768L*1024*1024))
                    throw new ImportCheckpointException($"Помещения {state.Next}/{ordered.Length} сохранены; освобождение истории Revit.");
            }
        }
        finally{source.Elements=original;}
        return state.Report;
    }
}
