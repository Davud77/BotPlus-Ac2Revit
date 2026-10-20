namespace Ac2Revit;

/// <summary>Explicit category scope and the physical dependencies required for it.</summary>
public sealed class ImportSelection
{
    public static readonly string[] KnownKinds=["Grid","Wall","Slab","Column","Beam","Door","Window","Opening","Zone","Roof","Mesh","Morph","Stair","Railing","CurtainWall","Object","Shell","Skylight","Lamp"];
    public HashSet<string> Requested {get;}
    public HashSet<string> Included {get;}
    public ImportSelection(IEnumerable<string> requested,IEnumerable<(string Child,string Host)>? hostKinds=null)
    {
        Requested=requested.ToHashSet(StringComparer.Ordinal);
        if(Requested.Count==0 || Requested.Any(k=>!KnownKinds.Contains(k)))throw new InvalidDataException("Выберите хотя бы одну поддержанную категорию.");
        Included=new(Requested,StringComparer.Ordinal);
        var dependencies=new List<(string Child,string Host)>{("Door","Wall"),("Window","Wall")};
        foreach(var host in new[]{"Wall","Slab","Column","Beam","Door","Window","Opening"})dependencies.Add(("Zone",host));
        if(hostKinds!=null)dependencies.AddRange(hostKinds);
        bool changed;
        do{changed=false;foreach(var (child,host) in dependencies)if(Included.Contains(child) && KnownKinds.Contains(host))changed|=Included.Add(host);}while(changed);
    }
    public bool Includes(string kind)=>Included.Contains(kind);
    public bool IncludesType(string kind)=>Includes(kind=="Floor"?"Slab":kind);
    public bool AllowsStage(string name)=>name switch
    {
        "grids" or "gridLabels"=>Includes("Grid"),
        "simpleColumns" or "columnBodies" or "profileColumns" or "columnBehavior" or "columnCompletion"=>Includes("Column"),
        "wallsSlabsMorphs"=>Included.Overlaps(["Wall","Slab","Morph"]),
        "wallCompletion"=>Includes("Wall"),
        "beamBodies" or "rectangularBeams" or "nativeCutBeams"=>Includes("Beam"),
        "openingsAndFillings"=>Included.Overlaps(["Door","Window","Opening"]),
        "supplementalBodies"=>Included.Overlaps(["Roof","Mesh","Stair","Railing","CurtainWall","Object","Shell","Skylight","Lamp"]),
        "roomSeparators" or "roomSeparatorRecheck"=>Includes("Zone"),
        "wallJoinsAndRooms"=>Includes("Wall") || Includes("Zone"),
        _=>true
    };
}
