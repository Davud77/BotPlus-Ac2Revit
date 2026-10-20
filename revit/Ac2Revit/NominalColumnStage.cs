using Autodesk.Revit.DB;

namespace Ac2Revit;

/// <summary>Explicitly incomplete API-only fallback, after verified IFC/native column stages.</summary>
public static class NominalColumnStage
{
    public static Report Run(Document doc,Manifest manifest,ImportConfig config,string output,Action<string,int,int>? progress=null)
    {
        var report=new Report{ProjectId=manifest.Source.ProjectId,Mode="nominalMissingColumns"};var index=Storage.Index(doc,manifest.Source.ProjectId);
        var geometry=new Geometry(doc,manifest.Coordinates.Transform);
        var adapter=new BasicColumnAdapter(doc,config,geometry,manifest.Coordinates.Transform,manifest.Source.ProjectId,report,nominalOnly:true);
        var selected=new List<ElementRecord>();
        foreach(var row in manifest.Elements.Where(e=>e.Kind=="Column" && !index.ContainsKey(e.SourceGuid)))
        {
            try{adapter.Validate(row);selected.Add(row);}
            catch(Exception ex){report.Add(row.SourceGuid,"NominalColumn","blocked",ex.Message);}
        }
        if(selected.Count>0)
        {
            adapter.Prepare(selected);int done=0;
            var materials=index.Where(p=>p.Key.StartsWith("material:") && p.Value is Material).ToDictionary(p=>p.Key[9..],p=>(Material)p.Value,StringComparer.OrdinalIgnoreCase);
            foreach(var row in selected)
            {
                using var transaction=new Transaction(doc,"Marked nominal column from API");
                try
                {
                    if(index.GetValueOrDefault(row.LevelKey) is not Level level)throw new InvalidDataException("Source column level missing");
                    transaction.Start();NativeGeometry.Configure(transaction,report,row.SourceGuid,"NominalColumn");
                    var column=adapter.Create(row,level,materials);
                    column.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)?.Set(row.Mark);
                    column.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)?.Set("НЕПОЛНОЦЕННАЯ КОЛОННА: нативная прямоугольная призма по размерам и положению API. Номинальные габариты/объём проверены; фактическая форма после подрезок и соединений не подтверждена IFC. GUID "+row.SourceGuid);
                    Storage.Tag(column,manifest.Source.ProjectId,row.SourceGuid,"nominal-column:"+row.ContentHash,row.Raw.GetRawText());
                    if(transaction.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Nominal column rolled back");
                    index[row.SourceGuid]=column;report.Add(row.SourceGuid,"NominalColumn","incomplete","API nominal rectangular column retained; IFC net geometry unavailable/unusable",column.UniqueId);
                }
                catch(Exception ex){if(transaction.GetStatus()==TransactionStatus.Started)transaction.RollBack();report.Add(row.SourceGuid,"NominalColumn","failed",ex.ToString());}
                progress?.Invoke("nominalColumns",++done,selected.Count);
            }
        }
        Directory.CreateDirectory(output);report.Save(Path.Combine(output,"nominal-columns.json"));return report;
    }
}
