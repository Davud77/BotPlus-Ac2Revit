using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using System.Text.Json;

namespace Ac2Revit;

public sealed class ImportCheckpointException(string message):Exception(message) { }

/// <summary>Release Revit undo/native caches by reopening a saved import checkpoint.</summary>
public static class PipelineExecution
{
    public static Report Run(UIApplication ui,string plan,string output,int limit=0,Action<string,int,int>? progress=null,bool preview=false)
    {
        Directory.CreateDirectory(output);int restarts=0;
        while(true)
        {
            string attempt=restarts==0?output:Path.Combine(output,$"resume-{restarts:00}");
            var doc=ui.ActiveUIDocument.Document;
            try{return PipelineRunner.Run(doc,plan,attempt,limit,progress,preview);}
            catch(ImportCheckpointException ex)when(!preview && restarts<128)
            {
                string model=doc.PathName;
                if(string.IsNullOrEmpty(model) || !File.Exists(model))throw new InvalidOperationException("Для продолжения после контрольной точки требуется сохранённый RVT.",ex);
                restarts++;progress?.Invoke("memoryCheckpoint",restarts,128);
                File.WriteAllText(Path.Combine(output,$"memory-restart-{restarts:00}.json"),JsonSerializer.Serialize(new{model,reason=ex.Message,restarts,utc=DateTime.UtcNow},Json.Options));
                // All transactions are finished and the stage saved the model
                // before throwing. The source PLN and other open documents stay intact.
                string parking=Path.Combine(output,$"checkpoint-parking-{restarts:00}.rvt");
                var temporary=ui.Application.NewProjectDocument(UnitSystem.Metric);
                temporary.SaveAs(parking,new SaveAsOptions{MaximumBackups=1});temporary.Close(false);
                var parked=ui.OpenAndActivateDocument(parking);doc.Close(false);
                ui.OpenAndActivateDocument(model);parked.Document.Close(false);
                GC.Collect();GC.WaitForPendingFinalizers();GC.Collect();
            }
        }
    }
}
