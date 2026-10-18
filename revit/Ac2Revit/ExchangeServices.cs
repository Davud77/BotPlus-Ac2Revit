using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace Ac2Revit;

/// <summary>Local Python worker, called without a shell; no Revit API on worker threads.</summary>
internal static class ExchangeServices
{
    public static string Root => Path.GetFullPath(Path.Combine(Path.GetDirectoryName(typeof(App).Assembly.Location)!, "..", ".."));
    public static string Sessions => Path.Combine(Root,"artifacts","sessions");
    public static string Assets => Path.Combine(Path.GetDirectoryName(typeof(App).Assembly.Location)!,"assets");
    public static string Python => Path.Combine(Root,".ifc-env","Scripts","python.exe");
    public static string ProjectTemplate(string fallback)
    {
        string config=Path.Combine(Root,"config","exchange-settings.json");
        if(File.Exists(config))
        {
            using var json=JsonDocument.Parse(File.ReadAllText(config));string value=json.RootElement.S("projectTemplate");
            if(value.Length>0){string path=Path.GetFullPath(value,Root);if(File.Exists(path))return path;}
        }
        return fallback;
    }
    public static string LastTemplate(string fallback)
    {
        using var pointer=JsonDocument.Parse(File.ReadAllText(Path.Combine(Sessions,"last-completed.json")));
        using var state=JsonDocument.Parse(File.ReadAllText(CheckedPath(pointer.RootElement.S("session"))));
        string saved=state.RootElement.S("templatePath");
        return File.Exists(saved)?saved:ProjectTemplate(fallback);
    }
    public static string CheckedPath(string path)
    {
        string full=Path.GetFullPath(path);
        if(!full.StartsWith(Root+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("Путь обмена находится вне рабочей папки.");
        return full;
    }
    public static async Task RunAsync(string script,IEnumerable<string> arguments,Action<string>? log=null,CancellationToken cancellation=default)
    {
        if(!File.Exists(Python))throw new FileNotFoundException("Не найден локальный Python среды транслятора",Python);
        var start=new ProcessStartInfo(Python){WorkingDirectory=Root,UseShellExecute=false,CreateNoWindow=true,
            RedirectStandardOutput=true,RedirectStandardError=true,StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};
        start.Environment["PYTHONUTF8"]="1";start.Environment["PYTHONUNBUFFERED"]="1";
        start.ArgumentList.Add(CheckedPath(Path.Combine(Root,"tools",script)));
        foreach(string value in arguments)start.ArgumentList.Add(value);
        using var process=new Process{StartInfo=start};
        process.Start();
        using var registration=cancellation.Register(()=>{try{if(!process.HasExited)process.Kill(entireProcessTree:true);}
            catch(Exception ex)when(ex is InvalidOperationException or System.ComponentModel.Win32Exception){} });
        var errors=new StringBuilder();
        async Task Read(StreamReader reader,bool error)
        {
            while(await reader.ReadLineAsync().ConfigureAwait(false) is string line)
            {if(error)errors.AppendLine(line);log?.Invoke(line);}
        }
        await Task.WhenAll(Read(process.StandardOutput,false),Read(process.StandardError,true),process.WaitForExitAsync()).ConfigureAwait(false);
        if(process.ExitCode==0)return; // A completed publication wins a late cancellation click.
        cancellation.ThrowIfCancellationRequested();
        if(process.ExitCode!=0)throw new InvalidOperationException(errors.Length>0?errors.ToString():"Экспорт не завершён. Подробности в журнале.");
    }
    public static void Run(string script,IEnumerable<string> args,Action<string>? log=null)=>RunAsync(script,args,log).GetAwaiter().GetResult();
    public static string LastSession()
    {
        string pointer=Path.Combine(Sessions,"last-completed.json");
        if(!File.Exists(pointer))throw new InvalidOperationException("Сначала выполните «Экспорт Archicad».");
        using var json=JsonDocument.Parse(File.ReadAllText(pointer));
        string path=CheckedPath(json.RootElement.S("session"));
        using var state=JsonDocument.Parse(File.ReadAllText(path));var s=state.RootElement;
        if(s.S("status")!="ready")throw new InvalidDataException("Последний экспорт не завершён.");
        string manifest=CheckedPath(s.S("manifest"));
        using var stream=File.OpenRead(manifest);
        if(!Convert.ToHexString(SHA256.HashData(stream)).Equals(s.S("manifestSha256"),StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("JSON после экспорта изменён. Выполните новый экспорт.");
        return CheckedPath(s.S("pipeline"));
    }
    public static void OpenFolder(string path)
    {
        Directory.CreateDirectory(path);
        Process.Start(new ProcessStartInfo("explorer.exe"){UseShellExecute=false,ArgumentList={Path.GetFullPath(path)}});
    }
}
