using Autodesk.Revit.DB;

namespace Ac2Revit;

public static class GridLabelStage
{
    public static Report Run(Document doc,Manifest manifest)
    {
        var report=new Report{ProjectId=manifest.Source.ProjectId,Mode="sourceGridLabels"};
        var index=Storage.Index(doc,manifest.Source.ProjectId);
        var occupied=new FilteredElementCollector(doc).OfClass(typeof(Grid)).Cast<Grid>().Select(g=>g.Name).ToHashSet(StringComparer.OrdinalIgnoreCase);
        using var t=new Transaction(doc,"Original visible grid labels");t.Start();NativeGeometry.Configure(t,report,"grid-labels","GridLabel");
        foreach(var record in manifest.Elements.Where(e=>e.Kind=="Grid").OrderBy(e=>e.SourceGuid,StringComparer.Ordinal))
        {
            if(index.GetValueOrDefault(record.SourceGuid) is not Grid grid)continue;
            string source=record.Payload.S("sourceLabel",record.Mark);
            if(string.IsNullOrWhiteSpace(source)){report.Add(record.SourceGuid,"GridLabel","incomplete","Исходная подпись оси пуста; существующее имя оставлено для проверки.",grid.UniqueId);continue;}
            if(GridLabels.Visible(grid.Name)==source){report.Add(record.SourceGuid,"GridLabel","verified",source,grid.UniqueId);continue;}
            occupied.Remove(grid.Name);string next=GridLabels.Unique(source,occupied);grid.Name=next;
            if(grid.Name!=next || GridLabels.Visible(grid.Name)!=source)throw new InvalidDataException("Revit changed the visible grid label");
            report.Add(record.SourceGuid,"GridLabel","updated","Исходная подпись: "+source+". Повторы различаются только невидимым символом; GUID хранится в данных элемента.",grid.UniqueId);
        }
        if(t.Commit()!=TransactionStatus.Committed)throw new InvalidDataException("Grid label update rolled back");
        return report;
    }
}
