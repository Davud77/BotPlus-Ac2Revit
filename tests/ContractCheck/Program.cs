using Ac2Revit;

Point P(double x,double y)=>new(){X=x,Y=y};
void Near(double actual,double expected,double tolerance=1e-9)
{if(Math.Abs(actual-expected)>tolerance)throw new Exception($"Source analytic area {actual:G17} != {expected:G17}");}
foreach(double sweep in new[]{Math.PI,-Math.PI,1.5*Math.PI,-1.5*Math.PI,1e-4})
{
    var arc=new Edge{Kind="arc",Start=P(0,0),Mid=P(1,-Math.Tan(sweep/4)),End=P(2,0)};
    double radius=1/Math.Abs(Math.Sin(sweep/2));
    Near(SourcePlanarGeometry.SignedArea(new[]{arc,new Edge{Start=P(2,0),End=P(0,0)}}),radius*radius*(sweep-Math.Sin(sweep))/2,1e-8);
}
var largeArc=new Edge{Kind="arc",Start=P(500000,-200000),Mid=P(500001,-200001),End=P(500002,-200000)};
Near(SourcePlanarGeometry.SignedArea(new[]{largeArc,new Edge{Start=largeArc.End,End=largeArc.Start}}),Math.PI/2);
Console.WriteLine("PASS: analytic minor/major/reversed/tiny arcs and large-coordinate area checks");
if(args.Length==0)return;

var manifest = Manifest.Load(args[0]);
var ordered = manifest.OrderedElements();
var position = ordered.Select((e, i) => (e.SourceGuid, i)).ToDictionary(x => x.SourceGuid, x => x.i);
foreach (var e in ordered)
    foreach (var h in e.Dependencies)
        if (position[h] >= position[e.SourceGuid]) throw new Exception("Host must precede child");
Console.WriteLine($"PASS: {manifest.Levels.Count} levels, {manifest.Types.Count} types, {ordered.Count} elements; GUIDs and dependencies valid");
if(args.Length>1)
{
    var rows=ordered.Where(e=>e.Kind=="Zone" && e.Status=="ready").Select(e=>
    {
        var loops=e.Payload.GetProperty("loops").Read<List<List<Edge>>>();
        double area=Math.Abs(SourcePlanarGeometry.SignedArea(loops[0]))-loops.Skip(1).Sum(l=>Math.Abs(SourcePlanarGeometry.SignedArea(l)));
        return new{sourceGuid=e.SourceGuid,areaM2=area,arcCount=loops.Sum(l=>l.Count(c=>c.Kind=="arc"))};
    }).ToArray();
    File.WriteAllText(args[1],System.Text.Json.JsonSerializer.Serialize(new{sourceManifest=Path.GetFullPath(args[0]),sourceUnits="m",areas=rows},Json.Options));
    Console.WriteLine($"PASS: {rows.Length} source room areas; {rows.Count(r=>r.arcCount>0)} have analytic arcs");
}
if (ordered.Count > 1)
{
    manifest.Elements.Add(ordered[0]);
    try { manifest.Validate(); throw new Exception("Duplicate GUID accepted"); }
    catch (InvalidDataException) { Console.WriteLine("PASS: duplicate GUID rejected"); }
}
