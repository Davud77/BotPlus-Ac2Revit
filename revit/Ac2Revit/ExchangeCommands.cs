using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Threading;
using Microsoft.Win32;

namespace Ac2Revit;

public sealed class AlwaysAvailable : IExternalCommandAvailability
{
    public bool IsCommandAvailable(UIApplication app,CategorySet categories)=>true;
}
[Transaction(TransactionMode.Manual)]
public sealed class ExportArchicadCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData data,ref string message,ElementSet elements)=>ExchangeCommands.Export(data.Application,false,ref message);
}
[Transaction(TransactionMode.Manual)]
public sealed class TransferCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData data,ref string message,ElementSet elements)=>ExchangeCommands.Export(data.Application,true,ref message);
}
[Transaction(TransactionMode.Manual)]
public sealed class BuildLatestCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData data,ref string message,ElementSet elements)
    {
        try
        {
            string path=ExchangeServices.LastSession();
            var dialog=new TaskDialog("Сборка экспорта"){MainInstruction="Куда собрать последний завершённый экспорт?",MainContent=path,
                CommonButtons=TaskDialogCommonButtons.Cancel};
            dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink1,"Создать новый проект Revit","Исходная модель Revit остаётся открытой.");
            if(data.Application.ActiveUIDocument?.Document is Document doc && !doc.IsFamilyDocument && !doc.IsReadOnly)
                dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink2,"Использовать текущий проект","Повторный импорт использует сохранённые GUID элементов.");
            var selected=dialog.Show();
            if(selected==TaskDialogResult.Cancel)return Result.Cancelled;
            if(selected==TaskDialogResult.CommandLink1)ExchangeCommands.NewProject(data.Application,path,ExchangeServices.LastTemplate(data.Application.Application.DefaultProjectTemplate));
            return Command.RunPath(data.Application,path,ref message);
        }
        catch(Exception ex){message=ex.Message;return Result.Failed;}
    }
}
[Transaction(TransactionMode.Manual)]
public sealed class ExchangeFolderCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData data,ref string message,ElementSet elements)
    {try{ExchangeServices.OpenFolder(ExchangeServices.Sessions);return Result.Succeeded;}catch(Exception ex){message=ex.Message;return Result.Failed;}}
}
[Transaction(TransactionMode.Manual)]
public sealed class ContinueSessionCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData data,ref string message,ElementSet elements)
    {
        try
        {
            var dialog=new OpenFileDialog{Title="Выберите session.json сохранённого экспорта",Filter="Сеанс Archicad|session.json|JSON|*.json",InitialDirectory=ExchangeServices.Sessions};
            if(dialog.ShowDialog()!=true)return Result.Cancelled;
            return ExchangeCommands.Export(data.Application,true,ref message,ExchangeServices.CheckedPath(dialog.FileName));
        }
        catch(Exception ex){message=ex.Message;return Result.Failed;}
    }
}
internal static class ExchangeCommands
{
    public static Result Export(UIApplication ui,bool build,ref string message,string resumeSession="")
    {
        try
        {
            var window=new ExchangeWindow(ui.MainWindowHandle,build,ui.ActiveUIDocument?.Document is Document d && !d.IsFamilyDocument && !d.IsReadOnly,ui.Application.DefaultProjectTemplate,resumeSession);
            if(window.ShowDialog()!=true)return Result.Cancelled;
            if(!build){TaskDialog.Show("Экспорт Archicad","Экспорт и подготовка завершены.\n\n"+window.PipelinePath+"\n\nНажмите «Собрать экспорт» для построения модели.");return Result.Succeeded;}
            if(window.CreateNewProject)NewProject(ui,window.PipelinePath,window.TemplatePath);
            return Command.RunPath(ui,window.PipelinePath,ref message);
        }
        catch(Exception ex){message=ex.Message;return Result.Failed;}
    }
    public static void NewProject(UIApplication ui,string pipeline,string template)
    {
        string output=Path.Combine(Path.GetDirectoryName(pipeline)!,"model-"+DateTime.Now.ToString("yyyyMMdd-HHmmssfff")+".rvt");
        // Save a new metric project before the pipeline, allowing stage checkpoints.
        var document=string.IsNullOrWhiteSpace(template)?ui.Application.NewProjectDocument(UnitSystem.Metric):ui.Application.NewProjectDocument(template);
        try{document.SaveAs(output,new SaveAsOptions{MaximumBackups=1});}
        finally{document.Close(false);}
        ui.OpenAndActivateDocument(output);
    }
}
internal sealed class ImportProgress : IDisposable
{
    readonly Window window;
    readonly TextBlock label=new(){Text="Подготовка импорта…",TextWrapping=TextWrapping.Wrap,Margin=new Thickness(18)};
    readonly ProgressBar bar=new(){Height=6,Margin=new Thickness(18,0,18,18)};
    bool closing;
    public ImportProgress(IntPtr owner)
    {
        var content=new StackPanel();content.Children.Add(label);content.Children.Add(bar);
        window=new Window{Title="Сборка модели Archicad",Width=520,Height=155,ResizeMode=ResizeMode.NoResize,
            WindowStartupLocation=WindowStartupLocation.CenterOwner,Content=content,ShowInTaskbar=false};
        new WindowInteropHelper(window){Owner=owner};
        window.Closing+=(_,e)=>{if(!closing)e.Cancel=true;};window.Show();
    }
    public void Update(string phase,int done,int total)
    {
        string title=phase.StartsWith("supplemental:",StringComparison.Ordinal)?"Дополнительная IFC-геометрия: "+phase[13..]:phase switch
        {
            "resources"=>"Ресурсы проекта", "levels"=>"Уровни", "materials"=>"Материалы", "types"=>"Типы конструкций",
            "prepareHatchMapping"=>"Подбор штриховок шаблона", "hatchMapping"=>"Штриховки материалов", "grids"=>"Оси",
            "simpleColumns"=>"Простые колонны", "columnBodies"=>"Геометрия сложных колонн", "profileColumns"=>"Профили колонн",
            "columnBehavior"=>"Конструктивное поведение колонн", "wallsSlabsMorphs"=>"Стены и перекрытия", "wallCompletion" or "missingWalls" or "wallGeometry" or "wallGeometryPreparation"=>"Дополнение стен",
            "beamBodies"=>"Геометрия балок", "rectangularBeams"=>"Прямоугольные балки", "nativeCutBeams"=>"Подрезки балок",
            "openingsAndFillings" or "openingCuts"=>"Проёмы", "fillingDoor"=>"Двери", "fillingWindow"=>"Окна",
            "roomSeparators"=>"Границы помещений", "wallJoinsAndRooms"=>"Сопряжения и помещения", "wallTopology"=>"Сопряжения стен",
            "zoneAreas"=>"Площади помещений", "roomBoundaryAudit"=>"Проверка границ помещений", "views"=>"Планы и 3D-вид",
            "elements"=>"Построение элементов", "referencePlanes"=>"Привязки стен", _=>phase
        };
        label.Text=$"Этап: {title}\nВыполнено: {done} / {total}. Дождитесь завершения сборки.";
        bar.Maximum=Math.Max(total,1);bar.Value=Math.Min(done,bar.Maximum);
        // Render messages only. Revit API stays on the original command thread.
        window.Dispatcher.Invoke(()=>{},DispatcherPriority.Render);
    }
    public void Dispose(){closing=true;window.Close();}
}
