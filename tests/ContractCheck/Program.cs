using Ac2Revit;

var zeroWall=new ElementRecord{Kind="Wall",Raw=System.Text.Json.JsonSerializer.Deserialize<System.Text.Json.JsonElement>("""
{"details":{"geometryType":"Straight","structureType":"Basic","profileType":"Normal","begThickness":0,"endThickness":0,"arcAngle":0,"offset":0,"zCoordinate":8.4,"height":3.3,"zoneRel":"Boundary","begCoordinate":{"x":-350,"y":-966},"endCoordinate":{"x":-350,"y":-965.8}}}
""")};
var zero=SourceZeroThicknessWall.Read(zeroWall);
if(!zero.RoomBoundary || Math.Abs(zero.Height-3.3)>1e-9 || zero.Curve.Start.Z!=8.4)throw new Exception("Zero-thickness source geometry changed");
foreach(string replacement in new[]{"\"begThickness\":0.001","\"begThickness\":-0.001","\"absentThickness\":0"})
{
    var other=new ElementRecord{Kind="Wall",Raw=System.Text.Json.JsonSerializer.Deserialize<System.Text.Json.JsonElement>(zeroWall.Raw.GetRawText().Replace("\"begThickness\":0",replacement))};
    if(SourceZeroThicknessWall.IsCandidate(other))throw new Exception("Nonzero/missing thickness treated as a zero-width boundary");
}
Console.WriteLine("PASS: zero-thickness source classification preserves axis/height and rejects missing/nonzero width");

long gb=1024L*1024*1024;
if(CheckpointPolicy.ShouldReopen(false,7*gb,10*gb))throw new Exception("Rejected bodies must not trigger a reopen");
if(CheckpointPolicy.ShouldReopen(true,9*gb,9*gb))throw new Exception("High baseline without growth must not loop");
if(!CheckpointPolicy.ShouldReopen(true,7*gb,9*gb))throw new Exception("Committed growth must checkpoint");
if(!CheckpointPolicy.ShouldReopen(true,9*gb,10*gb))throw new Exception("Growth after an already high baseline must checkpoint");
Console.WriteLine("PASS: checkpoint progress and memory-growth guards");
var gridNames=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
foreach(string label in new[]{"1*","1*","Н*","Н*","A","a"})
{
    string name=GridLabels.Unique(label,gridNames);
    if(GridLabels.Visible(name)!=label || name.Contains("IFC"))throw new Exception("Visible source grid label changed");
}
if(gridNames.Count!=6)throw new Exception("Duplicate visible grid labels were lost");
Console.WriteLine("PASS: repeated grid labels remain visually identical and internally unique");
var slabScope=new ImportSelection(["Slab"]);
if(!slabScope.AllowsStage("wallsSlabsMorphs") || slabScope.AllowsStage("wallCompletion") || slabScope.AllowsStage("wallJoinsAndRooms"))throw new Exception("Slab scope leaked into walls/rooms");
if(!slabScope.IncludesType("Floor") || slabScope.IncludesType("Wall"))throw new Exception("Unselected host types leaked into resources");
var windowScope=new ImportSelection(["Window"]);
if(!windowScope.Includes("Wall") || windowScope.Includes("Door") || windowScope.Includes("Zone"))throw new Exception("Window dependencies differ from the declared scope");
var voidScope=new ImportSelection(["Opening"],[("Opening","Slab"),("Opening","Beam")]);
if(!voidScope.Includes("Slab") || !voidScope.Includes("Beam") || voidScope.Includes("Object"))throw new Exception("Explicit opening hosts were not included precisely");
var roomScope=new ImportSelection(["Zone"]);
if(!roomScope.Includes("Column") || !roomScope.Includes("Wall") || roomScope.Includes("Object"))throw new Exception("Spatial dependency scope is incorrect");
try{_ =new ImportSelection([]);throw new Exception("Empty selection accepted");}catch(InvalidDataException){}
Console.WriteLine("PASS: category selection and physical host dependencies");

string referenceFile=Path.GetTempFileName();
try
{
    File.WriteAllText(referenceFile,"original IFC fixture");
    string hash=Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(referenceFile)));
    using(var scope=new VerifiedReferenceScope())
    {
        VerifiedReferenceScope.Verify(referenceFile,hash);
        VerifiedReferenceScope.Verify(referenceFile,hash.ToLowerInvariant());
        using(var reader=File.OpenRead(referenceFile))if(reader.Length==0)throw new Exception("Concurrent reference readers must work");
        try{using var writer=File.Open(referenceFile,FileMode.Open,FileAccess.Write,FileShare.ReadWrite);throw new Exception("Reference writes allowed during verified lease");}
        catch(IOException){}
        try{VerifiedReferenceScope.Verify(referenceFile,new string('0',64));throw new Exception("Conflicting expected hash accepted");}
        catch(InvalidDataException){}
    }
    File.WriteAllText(referenceFile,"changed after lease released");
    try{VerifiedReferenceScope.Verify(referenceFile,hash);throw new Exception("Changed file accepted by a new scope");}
    catch(InvalidDataException){}
    File.WriteAllText(referenceFile,"reopened after failed verification");
}
finally{File.Delete(referenceFile);}
Console.WriteLine("PASS: reference lease allows readers, blocks writes, rejects conflicting/changed inputs and releases handles");

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
