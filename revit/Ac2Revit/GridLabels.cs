namespace Ac2Revit;

/// <summary>Keep the visible source label; resolve uniqueness with invisible separators.</summary>
public static class GridLabels
{
    public const char Separator='\u200B';
    public static string Visible(string name)=>name.Replace("\u200B","").Replace("\u2060","");
    public static string Unique(string source,ISet<string> occupied)
    {
        if(string.IsNullOrWhiteSpace(source))throw new InvalidDataException("Source grid label is empty");
        string candidate=source;
        for(int i=0;!occupied.Add(candidate);i++)
        {
            if(i>=4096)throw new InvalidDataException("Too many identical grid labels");
            candidate+=Separator;
        }
        return candidate;
    }
}
