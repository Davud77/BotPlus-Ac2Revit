using Autodesk.Revit.DB;

namespace Ac2Revit;

/// <summary>Keep native cutting instances, but hide/pin only owned service voids.</summary>
public static class ServiceCutsStage
{
    public static Report Run(Document doc,string project)
    {
        var report=new Report{ProjectId=project,Mode="serviceVoidPresentation"};
        var cuts=Storage.Index(doc,project).Values.OfType<FamilyInstance>().Where(e=>
        {
            var tag=Storage.Read(e);return tag.HasValue && (tag.Value.Hash.StartsWith("opening-ifc:",StringComparison.Ordinal) || tag.Value.Key.StartsWith("beamcut:",StringComparison.Ordinal))
                && InstanceVoidCutUtils.IsVoidInstanceCuttingElement(e);
        }).ToList();
        using var t=new Transaction(doc,"Protect and hide owned service cuts");t.Start();NativeGeometry.Configure(t,report,"service-voids","ServiceVoid");
        foreach(var cut in cuts)
        {
            cut.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)?.Set("СЛУЖЕБНЫЙ ВЫРЕЗ ПРОЁМА/ПОДРЕЗКИ. Не удалять: без этого элемента стена или балка заполняет вырез. Геометрия окна/двери хранится отдельно. Исходный ключ: "+Storage.Read(cut)!.Value.Key);
            try{cut.Pinned=true;}catch(Exception ex){report.Add(Storage.Read(cut)!.Value.Key,"ServiceVoid","notice","Не удалось закрепить: "+ex.Message,cut.UniqueId);}
        }
        int hidden=0;
        foreach(var view in new FilteredElementCollector(doc).OfClass(typeof(View)).Cast<View>().Where(v=>!v.IsTemplate && v.ViewType is ViewType.ThreeD or ViewType.FloorPlan or ViewType.CeilingPlan or ViewType.EngineeringPlan or ViewType.AreaPlan or ViewType.Section or ViewType.Elevation or ViewType.Detail))
        {
            var ids=cuts.Where(c=>!c.IsHidden(view) && c.CanBeHidden(view)).Select(c=>c.Id).ToList();
            if(ids.Count==0)continue;
            try{view.HideElements(ids);hidden+=ids.Count;}
            catch(Exception ex){report.Add(view.UniqueId,"ServiceVoid","notice","Скрытие в виде недоступно: "+ex.Message);}
        }
        if(t.Commit()!=TransactionStatus.Committed)throw new InvalidDataException("Service void presentation rolled back");
        report.Add("service-voids","ServiceVoid","verified",$"Режущих экземпляров сохранено и обработано: {cuts.Count}; назначений скрытия: {hidden}. Окна, двери и прочие обобщённые модели не скрывались.");
        return report;
    }
}
