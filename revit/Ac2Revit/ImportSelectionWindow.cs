using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;

namespace Ac2Revit;

internal sealed class ImportSelectionWindow:Window
{
    public static string Label(string kind)=>kind switch
    {
        "Grid"=>"Оси","Wall"=>"Стены","Slab"=>"Перекрытия","Column"=>"Колонны","Beam"=>"Балки и ригели",
        "Door"=>"Двери","Window"=>"Окна","Opening"=>"Самостоятельные проёмы","Zone"=>"Помещения",
        "Roof"=>"Крыши","Mesh"=>"Рельеф","Morph"=>"Morph","Stair"=>"Лестницы","Railing"=>"Ограждения",
        "CurtainWall"=>"Навесные стены","Object"=>"Объекты","Shell"=>"Оболочки","Skylight"=>"Световые люки","Lamp"=>"Светильники",_=>kind
    };
    readonly Dictionary<string,CheckBox> boxes=[];
    readonly TextBlock dependencies=new(){TextWrapping=TextWrapping.Wrap,Margin=new Thickness(0,12,0,12)};
    readonly Button run=new(){Content="Начать импорт",Padding=new Thickness(18,7,18,7),IsDefault=true};
    readonly List<(string Child,string Host)> hostKinds=[];
    public ImportSelection? Selection {get;private set;}
    public ImportSelectionWindow(IntPtr owner,string path)
    {
        Title="BotPlus Ac2Revit — что переносить";Width=630;Height=680;MinHeight=500;MinWidth=530;WindowStartupLocation=WindowStartupLocation.CenterOwner;
        new WindowInteropHelper(this){Owner=owner};
        string sourcePath=path;Manifest? axes=null;
        if(PipelineRunner.IsPipeline(path))
        {
            var plan=JsonSerializer.Deserialize<PipelineRunner.Plan>(File.ReadAllText(path),Json.Options)!;
            string Resolve(string p)=>Path.GetFullPath(p,Path.GetDirectoryName(path)!);
            sourcePath=Resolve(plan.SourceManifest);axes=Manifest.Load(Resolve(plan.GridsManifest));
            var source=Manifest.Load(sourcePath);var kinds=source.Elements.ToDictionary(e=>e.SourceGuid,e=>e.Kind,StringComparer.OrdinalIgnoreCase);
            using var openings=JsonDocument.Parse(File.ReadAllText(Resolve(plan.OpeningsPlan)));
            foreach(var row in openings.RootElement.GetProperty("elements").EnumerateArray())
                if(kinds.TryGetValue(row.S("hostGuid"),out var host))hostKinds.Add((row.S("kind"),host));
        }
        var manifest=Manifest.Load(sourcePath);var counts=manifest.Elements.GroupBy(e=>e.Kind).ToDictionary(g=>g.Key,g=>g.Count());
        if(axes!=null)counts["Grid"]=axes.Elements.Count(e=>e.Kind=="Grid");
        var root=new DockPanel{Margin=new Thickness(20)};Content=root;
        var bottom=new StackPanel();DockPanel.SetDock(bottom,Dock.Bottom);root.Children.Add(bottom);bottom.Children.Add(dependencies);
        var actions=new StackPanel{Orientation=Orientation.Horizontal,HorizontalAlignment=HorizontalAlignment.Right};bottom.Children.Add(actions);
        var cancel=new Button{Content="Отмена",IsCancel=true,Margin=new Thickness(10,0,0,0),Padding=new Thickness(18,7,18,7)};
        actions.Children.Add(run);actions.Children.Add(cancel);
        var top=new StackPanel();DockPanel.SetDock(top,Dock.Top);root.Children.Add(top);
        top.Children.Add(new TextBlock{Text="Выберите элементы модели",FontSize=21,FontWeight=FontWeights.SemiBold,Margin=new Thickness(0,0,0,10)});
        top.Children.Add(new TextBlock{Text="Уровни, необходимые материалы и типы подготовятся автоматически. Снятие флажка не удаляет ранее созданные элементы.",TextWrapping=TextWrapping.Wrap,Margin=new Thickness(0,0,0,10)});
        var toggles=new StackPanel{Orientation=Orientation.Horizontal,Margin=new Thickness(0,0,0,8)};top.Children.Add(toggles);
        foreach(var (text,value) in new[]{("Выбрать всё",true),("Снять всё",false)})
        {var button=new Button{Content=text,Padding=new Thickness(10,4,10,4),Margin=new Thickness(0,0,8,0)};button.Click+=(_,_)=>{foreach(var box in boxes.Values)box.IsChecked=value;};toggles.Children.Add(button);}
        var list=new StackPanel();root.Children.Add(new ScrollViewer{Content=list,VerticalScrollBarVisibility=ScrollBarVisibility.Auto});
        foreach(string kind in ImportSelection.KnownKinds)
        {
            int count=counts.GetValueOrDefault(kind);if(count==0)continue;
            var box=new CheckBox{Content=$"{Label(kind)} — {count:N0}",IsChecked=true,Margin=new Thickness(3,5,3,5),FontSize=15};
            boxes[kind]=box;list.Children.Add(box);box.Checked+=(_,_)=>Refresh();box.Unchecked+=(_,_)=>Refresh();
        }
        run.Click+=(_,_)=>{Selection=Current();DialogResult=true;};Refresh();
    }
    ImportSelection Current()=>new(boxes.Where(p=>p.Value.IsChecked==true).Select(p=>p.Key),hostKinds);
    void Refresh()
    {
        if(!boxes.Values.Any(b=>b.IsChecked==true)){dependencies.Text="Выберите хотя бы одну категорию.";run.IsEnabled=false;return;}
        var scope=Current();run.IsEnabled=true;var extra=scope.Included.Except(scope.Requested).Select(Label).ToArray();
        dependencies.Text=extra.Length==0?"Импортируются выбранные категории и их вспомогательные ресурсы.":"Для выбранных элементов автоматически включены зависимости: "+string.Join(", ",extra)+".";
    }
}
