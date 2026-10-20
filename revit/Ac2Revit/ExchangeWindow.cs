using System.ComponentModel;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using Microsoft.Win32;

namespace Ac2Revit;

/// <summary>Modal UI with an asynchronous local worker; only the command uses Revit API.</summary>
internal sealed class ExchangeWindow : Window
{
    readonly TextBox port=new(){Text="19723",Width=82,VerticalContentAlignment=VerticalAlignment.Center};
    readonly Button connect=new(){Content="Подключиться",Padding=new Thickness(14,5,14,5)};
    readonly TextBlock source=new(){Text="Подключение к Archicad…",TextWrapping=TextWrapping.Wrap};
    readonly ComboBox translators=new(){MinHeight=30};
    readonly CheckBox properties=new(){Content="Пользовательские свойства элементов (увеличивает время экспорта)",Margin=new Thickness(0,10,0,6)};
    readonly CheckBox gdl=new(){Content="Все GDL-параметры — отдельный архив (может занять несколько ГБ)",Margin=new Thickness(0,0,0,6),ToolTip="Полные параметры сохраняются в source/raw; JSON содержит ссылки с SHA-256. Для построения геометрии этот флажок не требуется."};
    readonly CheckBox newProject=new(){Content="Собрать в новом проекте Revit",IsChecked=true,Margin=new Thickness(0,6,0,6)};
    readonly TextBox template=new(){MinHeight=28,VerticalContentAlignment=VerticalAlignment.Center};
    readonly Button browse=new(){Content="Шаблон…",Padding=new Thickness(10,3,10,3)};
    readonly TextBox log=new(){IsReadOnly=true,AcceptsReturn=true,TextWrapping=TextWrapping.Wrap,
        VerticalScrollBarVisibility=ScrollBarVisibility.Auto,Background=new SolidColorBrush(Color.FromRgb(245,247,250)),Padding=new Thickness(8)};
    readonly Button run=new(){MinWidth=190,Padding=new Thickness(14,8,14,8),IsEnabled=false};
    readonly Button cancel=new(){Content="Закрыть",MinWidth=90,Padding=new Thickness(14,8,14,8),Margin=new Thickness(8,0,0,0)};
    readonly ProgressBar progress=new(){Height=4,Margin=new Thickness(0,8,0,8)};
    JsonElement? connection;
    CancellationTokenSource? cancellation;
    bool busy;
    readonly bool build;
    readonly bool hasProject;
    string resumeSession;
    public string PipelinePath {get;private set;}="";
    public bool CreateNewProject=>newProject.IsChecked==true;
    public string TemplatePath=>template.Text.Trim();

    public ExchangeWindow(IntPtr owner,bool buildAfter,bool hasProject,string defaultTemplate,string resumeSession="")
    {
        build=buildAfter;this.hasProject=hasProject;this.resumeSession=resumeSession;
        Title="BotPlus Ac2Revit — "+(resumeSession.Length>0?"Продолжить сеанс":build?"Перенос модели":"Экспорт из Archicad");
        Width=790;Height=740;MinWidth=680;MinHeight=620;WindowStartupLocation=WindowStartupLocation.CenterOwner;
        new WindowInteropHelper(this){Owner=owner};
        var root=new DockPanel{Margin=new Thickness(20)};Content=root;
        var bottom=new StackPanel();DockPanel.SetDock(bottom,Dock.Bottom);root.Children.Add(bottom);
        bottom.Children.Add(progress);
        var buttons=new StackPanel{Orientation=Orientation.Horizontal,HorizontalAlignment=HorizontalAlignment.Right};
        bottom.Children.Add(buttons);buttons.Children.Add(run);buttons.Children.Add(cancel);
        run.Content=resumeSession.Length>0?"Подготовить и собрать":build?"Экспортировать и собрать":"Экспортировать";
        var top=new StackPanel();DockPanel.SetDock(top,Dock.Top);root.Children.Add(top);
        top.Children.Add(new TextBlock{Text=resumeSession.Length>0?"Сохранённые JSON + IFC → модель Revit":"Открытый Archicad → JSON + IFC → модель Revit",FontSize=20,FontWeight=FontWeights.SemiBold,Margin=new Thickness(0,0,0,12)});
        var ports=new StackPanel{Orientation=Orientation.Horizontal,Margin=new Thickness(0,0,0,12)};top.Children.Add(ports);
        ports.Children.Add(new TextBlock{Text="Tapir · 127.0.0.1 : ",VerticalAlignment=VerticalAlignment.Center});ports.Children.Add(port);
        connect.Margin=new Thickness(10,0,0,0);ports.Children.Add(connect);
        top.Children.Add(new Border{Background=new SolidColorBrush(Color.FromRgb(235,244,252)),Padding=new Thickness(12),Child=source,Margin=new Thickness(0,0,0,12)});
        top.Children.Add(new TextBlock{Text=resumeSession.Length>0?"Подготовка сохранённых JSON и IFC":"IFC-транслятор Archicad",Margin=new Thickness(0,0,0,5)});top.Children.Add(translators);
        if(resumeSession.Length>0)translators.Visibility=Visibility.Collapsed;
        top.Children.Add(new TextBlock{Text=resumeSession.Length>0?"JSON восстанавливается из завершённых ответов API. Сохранённый IFC используется повторно; отсутствующий IFC экспортируется после сверки всех элементов. Откройте тот же PLN и не редактируйте модель во время проверки.":"Выберите переводчик, сохраняющий исходные координаты. GUID сверяются по данным Archicad и IFC. Экспортируется весь проект; во время чтения не редактируйте Archicad.",TextWrapping=TextWrapping.Wrap,FontSize=12,Margin=new Thickness(0,6,0,0)});
        top.Children.Add(properties);
        top.Children.Add(gdl);
        if(resumeSession.Length>0){properties.Visibility=Visibility.Collapsed;gdl.Visibility=Visibility.Collapsed;}
        if(build)
        {
            newProject.IsEnabled=hasProject;top.Children.Add(newProject);
            string preferred=ExchangeServices.ProjectTemplate(defaultTemplate);
            if(resumeSession.Length>0 && File.Exists(resumeSession))
            {
                using var saved=JsonDocument.Parse(File.ReadAllText(resumeSession));
                // An explicit empty choice means a metric project. Preserve it,
                // and let Export explain a missing template instead of silently
                // substituting a different one after the memory handoff.
                if(saved.RootElement.TryGetProperty("templatePath",out var selected))preferred=selected.GetString()??"";
            }
            template.Text=preferred;
            var target=new DockPanel{Margin=new Thickness(0,0,0,8)};DockPanel.SetDock(browse,Dock.Right);target.Children.Add(browse);target.Children.Add(template);top.Children.Add(target);
            top.Children.Add(new TextBlock{Text="Шаблон нового проекта: оставьте пустым для метрического проекта Revit. Для сопоставления штриховок используйте ваш архитектурный шаблон.",TextWrapping=TextWrapping.Wrap,FontSize=12,Margin=new Thickness(0,0,0,8)});
        }
        top.Children.Add(new TextBlock{Text="Журнал выполнения",FontWeight=FontWeights.SemiBold,Margin=new Thickness(0,4,0,6)});
        root.Children.Add(log);
        connect.Click+=async(_,_)=>await Connect();
        run.Click+=async(_,_)=>await Export();
        cancel.Click+=(_,_)=>{if(busy){cancellation?.Cancel();cancel.IsEnabled=false;}else Close();};
        browse.Click+=(_,_)=>{var d=new OpenFileDialog{Filter="Шаблон Revit|*.rte",Title="Шаблон нового проекта"};if(d.ShowDialog(this)==true)template.Text=d.FileName;};
        newProject.Checked+=(_,_)=>{template.IsEnabled=true;browse.IsEnabled=true;};
        newProject.Unchecked+=(_,_)=>{template.IsEnabled=false;browse.IsEnabled=false;};
        port.TextChanged+=(_,_)=>{connection=null;run.IsEnabled=false;};
        Closing+=OnClosing;
        Loaded+=async(_,_)=>await Connect();
    }
    void Log(string line)=>Dispatcher.BeginInvoke(new Action(()=>{log.AppendText(line+Environment.NewLine);log.ScrollToEnd();}));
    void SetBusy(bool value)
    {
        busy=value;connect.IsEnabled=!value;port.IsEnabled=!value;translators.IsEnabled=!value && resumeSession.Length==0;properties.IsEnabled=!value;gdl.IsEnabled=!value;
        run.IsEnabled=!value && connection!=null;newProject.IsEnabled=!value && hasProject;
        template.IsEnabled=!value && newProject.IsChecked==true;browse.IsEnabled=template.IsEnabled;
        cancel.IsEnabled=true;cancel.Content=value?"Остановить":"Закрыть";progress.IsIndeterminate=value;
    }
    async Task Connect()
    {
        if(busy)return;
        connection=null;translators.Items.Clear();
        if(!int.TryParse(port.Text,out int number)||number<1024||number>65535){source.Text="Введите правильный порт Tapir.";return;}
        SetBusy(true);source.Text="Чтение открытого проекта Archicad…";
        cancellation=new CancellationTokenSource();
        try
        {
            if(resumeSession.Length>0 && File.Exists(Path.Combine(Path.GetDirectoryName(resumeSession)!,"capture.json")))
            {
                using var saved=JsonDocument.Parse(File.ReadAllText(resumeSession));connection=saved.RootElement.Clone();
                var capturedProject=connection.Value.GetProperty("project");
                source.Text=capturedProject.S("projectName")+"\nСохранённый снимок JSON + IFC. Archicad можно закрыть; контрольные суммы будут проверены перед подготовкой.";
                Log("Автономное продолжение сохранённого захвата. Подключение к Archicad не требуется.");return;
            }
            string result=Path.Combine(ExchangeServices.Sessions,"connection.json");Directory.CreateDirectory(ExchangeServices.Sessions);
            await ExchangeServices.RunAsync("revit_session.py",["probe","--port",number.ToString(),"--result",result],Log,cancellation.Token);
            using var parsed=JsonDocument.Parse(File.ReadAllText(result));connection=parsed.RootElement.Clone();
            var info=connection.Value;var project=info.GetProperty("project");
            source.Text=project.S("projectName")+"\n"+project.S("projectPath")+"\nID: "+info.S("projectId");
            foreach(var t in info.GetProperty("translators").EnumerateArray())translators.Items.Add(t.S("name"));
            translators.SelectedItem=translators.Items.Cast<string>().FirstOrDefault(s=>s=="Точный Экспорт Геометрии");
            if(translators.SelectedIndex<0 && translators.Items.Count>0)translators.SelectedIndex=0;
            if(translators.Items.Count==0 && resumeSession.Length==0)throw new InvalidDataException("В Archicad не найдены IFC-трансляторы.");
            Log("Подключено. Читается весь проект, без фильтра видимости.");
            if(resumeSession.Length>0)Log("Восстановление и подготовка: "+resumeSession+". Готовый IFC используется повторно; отсутствующий экспортируется после проверки источника.");
        }
        catch(OperationCanceledException){source.Text="Подключение остановлено.";}
        catch(Exception ex){connection=null;source.Text="Archicad недоступен. Откройте сохранённый PLN с Tapir и проверьте порт.";Log(ex.Message);}
        finally{cancellation.Dispose();cancellation=null;SetBusy(false);}
    }
    async Task Export()
    {
        if(busy || connection==null || (resumeSession.Length==0 && translators.SelectedItem is not string))return;
        string translator=translators.SelectedItem as string??"";
        if(build && CreateNewProject && TemplatePath.Length>0 && !File.Exists(TemplatePath)){Log("Шаблон Revit не найден.");return;}
        var info=connection.Value;
        string name=info.GetProperty("project").S("projectName","Archicad");
        name=new string(name.Select(c=>Path.GetInvalidFileNameChars().Contains(c)?'_':c).ToArray());
        if(name.Length>70)name=name[..70];
        string session=resumeSession.Length>0?Path.GetDirectoryName(resumeSession)!:Path.Combine(ExchangeServices.Sessions,name+"-"+DateTime.Now.ToString("yyyyMMdd-HHmmssfff"));
        var args=new List<string>{"export","--port",((int)info.D("port")).ToString(),"--project-id",info.S("projectId"),
            "--project-path",info.GetProperty("project").S("projectPath"),"--translator",translator,"--output",session,"--assets",ExchangeServices.Assets};
        if(properties.IsChecked==true)args.Add("--properties");
        if(gdl.IsChecked==true)args.Add("--gdl");
        if(resumeSession.Length>0)args=["prepare","--session",resumeSession,"--port",((int)info.D("port")).ToString(),"--assets",ExchangeServices.Assets];
        SetBusy(true);cancellation=new CancellationTokenSource();
        using var journal=new StreamWriter(Path.Combine(ExchangeServices.Sessions,"export-"+DateTime.Now.ToString("yyyyMMdd-HHmmssfff")+".log"),false,System.Text.Encoding.UTF8){AutoFlush=true};
        object sync=new();
        try
        {
            Log("Новый сеанс: "+session);
            await ExchangeServices.RunAsync("revit_session.py",args,line=>{lock(sync)journal.WriteLine(line);Log(line);},cancellation.Token);
            using var state=JsonDocument.Parse(File.ReadAllText(Path.Combine(session,"session.json")));
            if(build && CreateNewProject)
            {
                var saved=System.Text.Json.Nodes.JsonNode.Parse(File.ReadAllText(Path.Combine(session,"session.json")))!;
                saved["templatePath"]=TemplatePath;File.WriteAllText(Path.Combine(session,"session.json"),saved.ToJsonString(Json.Options));
            }
            if(state.RootElement.S("status")=="captured")
            {
                resumeSession=Path.Combine(session,"session.json");connection=state.RootElement.Clone();run.Content="Продолжить подготовку";
                source.Text="Данные полностью захвачены. Для подготовки большой IFC-модели освободите память: закройте Archicad и ненужные модели Revit, затем нажмите «Продолжить подготовку».";
                Log("Сохранённый снимок: "+resumeSession+". Предыдущий готовый сеанс не изменён.");return;
            }
            if(state.RootElement.S("status")!="ready")throw new InvalidDataException("Сеанс ещё не готов к сборке.");
            PipelinePath=ExchangeServices.LastSession();
            if(!string.Equals(PipelinePath,Path.GetFullPath(state.RootElement.S("pipeline")),StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("Указатель последнего экспорта не совпал с текущим сеансом.");
            DialogResult=true;
        }
        catch(OperationCanceledException)
        {
            Log("Остановлен локальный обработчик. Неполный сеанс не опубликован для сборки. Уже запущенная операция Archicad может завершиться отдельно.");
            // Kill cannot execute the worker's exception handler. Mark its partial session explicitly.
            string statePath=Path.Combine(session,"session.json");
            if(resumeSession.Length==0 && File.Exists(statePath)){var node=System.Text.Json.Nodes.JsonNode.Parse(File.ReadAllText(statePath))!;if(node["status"]?.GetValue<string>()!="ready"){node["status"]="cancelled";File.WriteAllText(statePath,node.ToJsonString(Json.Options));}}
        }
        catch(Exception ex){Log(ex.Message);Log("Сеанс и диагностика: "+session);}
        finally{cancellation.Dispose();cancellation=null;SetBusy(false);}
    }
    void OnClosing(object? sender,CancelEventArgs e)
    {
        if(busy && DialogResult!=true){e.Cancel=true;Log("Операция выполняется. Чтобы остановить локальный обработчик, нажмите «Остановить».");}
    }
}
