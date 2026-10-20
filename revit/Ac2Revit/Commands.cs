using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Microsoft.Win32;
using System.Reflection;

namespace Ac2Revit;

public sealed class App : IExternalApplication
{
    public Result OnStartup(UIControlledApplication app)
    {
        const string tab="BotPlus Ac2Revit";
        try{app.CreateRibbonTab(tab);}catch(Autodesk.Revit.Exceptions.ArgumentException){ /* Tab already exists. */ }
        var panel = app.CreateRibbonPanel(tab,"Перенос модели");
        var assembly = Assembly.GetExecutingAssembly().Location;
        void Add(RibbonPanel target,string id,string label,Type command,string icon,string tooltip,bool noDocument=false)
        {
            var button=new PushButtonData(id,label,assembly,command.FullName){Image=RibbonIcons.Make(icon,16),LargeImage=RibbonIcons.Make(icon,32),ToolTip=tooltip};
            if(noDocument)button.AvailabilityClassName=typeof(AlwaysAvailable).FullName;
            target.AddItem(button);
        }
        Add(panel,"AcTransfer","Archicad\n→ Revit",typeof(TransferCommand),"transfer","Выгрузить открытый проект Archicad через Tapir и собрать его в Revit. JSON, IFC и отчёты сохраняются в папке обмена.",true);
        Add(panel,"AcExport","Экспорт\nArchicad",typeof(ExportArchicadCommand),"export","Прочитать открытый PLN, экспортировать JSON и IFC, подготовить этапы импорта. Работает без открытой модели Revit.",true);
        Add(panel,"AcBuildLatest","Собрать\nэкспорт",typeof(BuildLatestCommand),"build","Собрать последний успешно подготовленный экспорт. Неполные выгрузки не используются.",true);
        Add(panel,"AcContinueSession","Продолжить\nсеанс",typeof(ContinueSessionCommand),"resume","Восстановить JSON из архива и продолжить подготовку. Готовый IFC используется повторно; отсутствующий экспортируется после проверки источника.",true);
        var files=app.CreateRibbonPanel(tab,"Файлы и проверка");
        Add(files,"AcPreview","Проверить\nJSON",typeof(PreviewCommand),"preview","Выбрать manifest.json или pipeline.json и проверить план импорта.");
        Add(files,"AcImport","Импорт\nJSON",typeof(ImportCommand),"import","Выбрать подготовленный JSON и выполнить импорт в открытый проект Revit.");
        Add(files,"AcExchangeFolder","Папка\nобмена",typeof(ExchangeFolderCommand),"folder","Открыть сеансы экспорта, промежуточные данные, модели и отчёты.",true);
        RuntimeJobs.Start(app, assembly);
        return Result.Succeeded;
    }
    public Result OnShutdown(UIControlledApplication app) { RuntimeJobs.Stop(app); return Result.Succeeded; }
}
[Transaction(TransactionMode.Manual)]
public sealed class PreviewCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements) => Command.Execute(data, ref message, true);
}
[Transaction(TransactionMode.Manual)]
public sealed class ImportCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements) => Command.Execute(data, ref message, false);
}
public static class Command
{
    public static Result Execute(ExternalCommandData data, ref string message, bool preview)
    {
        if (data.Application.ActiveUIDocument == null) { message = "Откройте проект Revit"; return Result.Failed; }
        var dialog = new OpenFileDialog { Filter = "Archicad manifest / pipeline JSON|*.json", Title = "Выберите manifest.json или pipeline.json" };
        if (dialog.ShowDialog() != true) return Result.Cancelled;
        return RunPath(data.Application,dialog.FileName,ref message,preview);
    }
    public static Result RunPath(UIApplication ui,string path,ref string message,bool preview=false)
    {
        if(ui.ActiveUIDocument==null || ui.ActiveUIDocument.Document.IsFamilyDocument){message="Откройте проект Revit, не семейство.";return Result.Failed;}
        string folder = Path.GetDirectoryName(path)!;
        string reportPath = Path.Combine(folder, preview ? "revit-preview.json" : "revit-import-report.json");
        Importer? importer = null;
        try
        {
            ImportSelection? selection=null;
            if(!preview)
            {
                var choice=new ImportSelectionWindow(ui.MainWindowHandle,path);
                if(choice.ShowDialog()!=true)return Result.Cancelled;
                selection=choice.Selection;
            }
            if(PipelineRunner.IsPipeline(path))
            {
                string output=Path.Combine(folder,"revit-run-"+DateTime.UtcNow.ToString("yyyyMMdd-HHmmssfff"));
                using var display=preview?null:new ImportProgress(ui.MainWindowHandle);
                var pipelineReport=PipelineExecution.Run(ui,path,output,
                    progress:(phase,done,total)=>{File.WriteAllText(Path.Combine(output,"progress.json"),System.Text.Json.JsonSerializer.Serialize(new{phase,done,total,updatedUtc=DateTime.UtcNow},Json.Options));display?.Update(phase,done,total);},preview:preview,selection:selection);
                if(preview)Directory.CreateDirectory(output);
                pipelineReport.Save(Path.Combine(output,"pipeline-report.json"));
                string counts=string.Join("\n",pipelineReport.Items.GroupBy(x=>x.Status).Select(g=>g.Key+": "+g.Count()));
                TaskDialog.Show("Archicad JSON",counts+"\n\nПорядок: оси → колонны → стены → балки/подрезки → проёмы/заполнения → помещения.\nОтчёт: "+output);
                return Result.Succeeded;
            }
            var manifest = Manifest.Load(path);
            if(selection!=null)manifest.Types=manifest.Types.Where(t=>selection.IncludesType(t.Kind)).ToList();
            var config = ImportConfig.Load(Path.Combine(folder, "mapping.json"));
            importer = new Importer(ui.ActiveUIDocument.Document, manifest, config);
            var report = preview ? importer.Preview() : importer.Run(selection?.Included);
            report.Save(reportPath);
            string summary = string.Join("\n", report.Items.GroupBy(x => x.Status).Select(g => g.Key + ": " + g.Count()));
            TaskDialog.Show("Archicad JSON", summary + "\n\nОтчёт: " + reportPath);
            return Result.Succeeded;
        }
        catch (Exception ex)
        {
            message = ex.Message;
            File.WriteAllText(Path.Combine(folder, "revit-fatal-error.txt"), ex.ToString());
            try { importer?.Report.Save(reportPath); } catch { /* Preserve original failure. */ }
            return Result.Failed;
        }
    }
}
