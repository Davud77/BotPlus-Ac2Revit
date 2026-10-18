using Autodesk.Revit.DB;
using Autodesk.Revit.DB.ExtensibleStorage;
using System.Security.Cryptography;
using System.Text.Json;

namespace Ac2Revit;

/// <summary>Explicit material cut-pattern mapping. Never guesses by material name inside Revit.</summary>
public static class HatchMappingStage
{
    public sealed class Plan
    {
        public string Format {get;set;}="";
        public string ProjectId {get;set;}="";
        public string SourceManifestSha256 {get;set;}="";
        public List<Row> Materials {get;set;}=[];
    }
    public sealed class Row
    {
        public string MaterialGuid {get;set;}="";
        public int SourceFillIndex {get;set;}
        public string SourceFillGuid {get;set;}="";
        public string SourceFillName {get;set;}="";
        public string Match {get;set;}="";
        public string Reason {get;set;}="";
        public string TargetName {get;set;}="";
        public string TargetFingerprint {get;set;}="";
        public int[] ForegroundRgb {get;set;}=[0,0,0];
        public string Background {get;set;}="transparent";
        public int[] BackgroundRgb {get;set;}=[255,255,255];
        public string BackgroundFingerprint {get;set;}="";
    }
    static int[] Rgb(Color c)=>c.IsValid?[c.Red,c.Green,c.Blue]:[];
    public static object Pattern(FillPattern pattern)=>new{
        target=pattern.Target.ToString(),orientation=pattern.HostOrientation.ToString(),solid=pattern.IsSolidFill,
        grids=pattern.GetFillGrids().Select(g=>new{angle=g.Angle,origin=new[]{g.Origin.U,g.Origin.V},offset=g.Offset,shift=g.Shift,segments=g.GetSegments().ToArray()}).ToArray()};
    static object State(Material material)=>new{foreground=material.CutForegroundPatternId.Value,foregroundRgb=Rgb(material.CutForegroundPatternColor),
        background=material.CutBackgroundPatternId.Value,backgroundRgb=Rgb(material.CutBackgroundPatternColor)};
    static string StateKey(string guid)=>"hatch-state:"+guid.ToUpperInvariant();
    static JsonElement Metadata(Element element)
    {
        var schema=Storage.Schema();var entity=element.GetEntity(schema);
        return JsonSerializer.Deserialize<JsonElement>(entity.Get<string>(schema.GetField("RawJson")),Json.Options);
    }
    public static object Inventory(Document doc,Manifest manifest,string output)
    {
        var index=Storage.Index(doc,manifest.Source.ProjectId);
        var patterns=new FilteredElementCollector(doc).OfClass(typeof(FillPatternElement)).Cast<FillPatternElement>().OrderBy(p=>p.Name,StringComparer.Ordinal).Select(e=>{
            using var pattern=e.GetFillPattern();var geometry=Pattern(pattern);return new{id=e.Id.Value,name=e.Name,fingerprint=Json.Hash(geometry),geometry};}).ToArray();
        var compound=new FilteredElementCollector(doc).OfClass(typeof(HostObjAttributes)).Cast<HostObjAttributes>().Select(t=>new{
            typeId=t.Id.Value,name=t.Name,kind=t.GetType().Name,layers=t.GetCompoundStructure()?.GetLayers().Select(l=>new{materialId=l.MaterialId.Value,widthFeet=l.Width,function=l.Function.ToString()}).ToArray()}).ToArray();
        var materials=manifest.Materials.Where(m=>m.TryGetProperty("attributeId",out _)).Select(raw=>{
            string guid=raw.GetProperty("attributeId").S("guid").ToUpperInvariant();var material=index.GetValueOrDefault("material:"+guid) as Material;
            return new{sourceGuid=guid,sourceName=raw.S("name"),sourceFillIndex=raw.D("cutFillIndex"),elementId=material?.Id.Value,
                name=material?.Name,state=material==null?null:State(material)};}).ToArray();
        var result=new{format="ac2r.revit-hatch-catalog/1",projectId=manifest.Source.ProjectId,document=doc.PathName,patterns,materials,compoundTypes=compound};
        File.WriteAllText(output,JsonSerializer.Serialize(result,Json.Options));return new{patterns=patterns.Length,importedMaterials=materials.Count(m=>m.elementId!=null),output};
    }
    static Plan Load(Manifest manifest,string manifestPath,string input)
    {
        var plan=JsonSerializer.Deserialize<Plan>(File.ReadAllText(input),Json.Options)??throw new InvalidDataException("Empty hatch mapping");
        using var stream=File.OpenRead(manifestPath);
        if(plan.Format!="ac2r.hatch-mapping/1" || plan.ProjectId!=manifest.Source.ProjectId
            || !Convert.ToHexString(SHA256.HashData(stream)).Equals(plan.SourceManifestSha256,StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("Different hatch source snapshot/project");
        return plan;
    }
    static ElementId BackgroundId(Document doc,Row row)
    {
        if(row.Background=="transparent")return ElementId.InvalidElementId;
        var candidates=new FilteredElementCollector(doc).OfClass(typeof(FillPatternElement)).Cast<FillPatternElement>().Where(e=>{
            using var p=e.GetFillPattern();return p.Target==FillPatternTarget.Drafting && p.IsSolidFill && Json.Hash(Pattern(p))==row.BackgroundFingerprint;}).ToArray();
        if(candidates.Length!=1)throw new InvalidDataException("Missing/ambiguous solid background pattern");return candidates[0].Id;
    }
    static List<(Row Row,Material Material,ElementId Pattern)> Prepare(Document doc,Manifest manifest,Plan plan)
    {
        var index=Storage.Index(doc,plan.ProjectId);var guids=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var source=manifest.Materials.Where(m=>m.TryGetProperty("attributeId",out _)).ToDictionary(m=>m.GetProperty("attributeId").S("guid"),StringComparer.OrdinalIgnoreCase);
        var patterns=new FilteredElementCollector(doc).OfClass(typeof(FillPatternElement)).Cast<FillPatternElement>().ToArray();
        var result=new List<(Row,Material,ElementId)>();
        foreach(var row in plan.Materials)
        {
            if(!Guid.TryParse(row.MaterialGuid,out _) || !Guid.TryParse(row.SourceFillGuid,out _) || !guids.Add(row.MaterialGuid)
                || !source.TryGetValue(row.MaterialGuid,out var raw) || raw.D("cutFillIndex")!=row.SourceFillIndex
                || row.Match is not ("analogue" or "empty" or "solid") || string.IsNullOrWhiteSpace(row.Reason)
                || row.ForegroundRgb.Length!=3 || row.ForegroundRgb.Any(v=>v<0 || v>255) || row.Background is not ("transparent" or "solid")
                || row.BackgroundRgb.Length!=3 || row.BackgroundRgb.Any(v=>v<0 || v>255))throw new InvalidDataException("Invalid or duplicate explicit hatch mapping");
            if(index.GetValueOrDefault("material:"+row.MaterialGuid.ToUpperInvariant()) is not Material material)throw new InvalidDataException("Source material not imported: "+row.MaterialGuid);
            ElementId patternId=ElementId.InvalidElementId;
            if(row.Match!="empty")
            {
                var candidates=patterns.Where(e=>e.Name==row.TargetName).Where(e=>{using var p=e.GetFillPattern();return p.Target==FillPatternTarget.Drafting && Json.Hash(Pattern(p))==row.TargetFingerprint;}).ToArray();
                if(candidates.Length!=1)throw new InvalidDataException("Missing/changed/ambiguous drafting hatch: "+row.TargetName);
                using var pattern=candidates[0].GetFillPattern();
                if(pattern.IsSolidFill!=(row.Match=="solid"))throw new InvalidDataException("Solid versus line hatch mapping differs");
                patternId=candidates[0].Id;
            }
            BackgroundId(doc,row);result.Add((row,material,patternId));
        }
        return result;
    }
    public static Report Run(Document doc,Manifest manifest,string manifestPath,string input,string output)
    {
        var plan=Load(manifest,manifestPath,input);var prepared=Prepare(doc,manifest,plan);var index=Storage.Index(doc,plan.ProjectId);
        var report=new Report{ProjectId=plan.ProjectId,Mode="hatchMapping"};
        // A changed mapped material is a user edit, not permission to silently overwrite it on resume.
        foreach(var row in prepared)
            if(index.GetValueOrDefault(StateKey(row.Row.MaterialGuid)) is Element state && Metadata(state).S("stateHash")!=Json.Hash(State(row.Material)))
                throw new InvalidDataException("Mapped material graphics changed after previous import: "+row.Material.Name);
        using var group=new TransactionGroup(doc,"Mapped Archicad material cut patterns");group.Start();
        try
        {
            using var transaction=new Transaction(doc,"Material cut hatch mapping");transaction.Start();NativeGeometry.Configure(transaction,report,"hatch-mapping","HatchMapping");
            foreach(var row in prepared)
            {
                string before=Json.Hash(State(row.Material));
                if(row.Material.CutForegroundPatternId!=row.Pattern)row.Material.CutForegroundPatternId=row.Pattern;
                var colour=new Color((byte)row.Row.ForegroundRgb[0],(byte)row.Row.ForegroundRgb[1],(byte)row.Row.ForegroundRgb[2]);
                if(!Rgb(row.Material.CutForegroundPatternColor).SequenceEqual(row.Row.ForegroundRgb))row.Material.CutForegroundPatternColor=colour;
                var background=BackgroundId(doc,row.Row);
                if(row.Material.CutBackgroundPatternId!=background)row.Material.CutBackgroundPatternId=background;
                if(background!=ElementId.InvalidElementId && !Rgb(row.Material.CutBackgroundPatternColor).SequenceEqual(row.Row.BackgroundRgb))
                    row.Material.CutBackgroundPatternColor=new Color((byte)row.Row.BackgroundRgb[0],(byte)row.Row.BackgroundRgb[1],(byte)row.Row.BackgroundRgb[2]);
                string stateKey=StateKey(row.Row.MaterialGuid);var state=index.GetValueOrDefault(stateKey) as DataStorage;
                if(state==null){state=DataStorage.Create(doc);index.Add(stateKey,state);}
                string after=Json.Hash(State(row.Material));
                Storage.Tag(state,plan.ProjectId,stateKey,Json.Hash(row.Row),JsonSerializer.Serialize(new{mapping=row.Row,stateHash=after,materialUniqueId=row.Material.UniqueId},Json.Options));
                report.Add(row.Row.MaterialGuid,"MaterialCutHatch",before==after?"unchanged":"updated",$"{row.Row.Match}: {row.Row.SourceFillName} -> {(row.Row.Match=="empty"?"<none>":row.Row.TargetName)}. {row.Row.Reason}",row.Material.UniqueId);
            }
            if(transaction.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Cut hatch transaction rolled back");
            Audit(doc,manifest,manifestPath,input,output);group.Assimilate();
        }
        catch{group.RollBack();throw;}
        return report;
    }
    public static object Audit(Document doc,Manifest manifest,string manifestPath,string input,string output)
    {
        var plan=Load(manifest,manifestPath,input);var prepared=Prepare(doc,manifest,plan);var rows=prepared.Select(r=>new{
            sourceGuid=r.Row.MaterialGuid,materialId=r.Material.Id.Value,materialName=r.Material.Name,sourceFill=r.Row.SourceFillName,match=r.Row.Match,targetName=r.Row.TargetName,
            verified=r.Material.CutForegroundPatternId==r.Pattern && Rgb(r.Material.CutForegroundPatternColor).SequenceEqual(r.Row.ForegroundRgb)
                && r.Material.CutBackgroundPatternId==BackgroundId(doc,r.Row)
                && (r.Row.Background=="transparent" || Rgb(r.Material.CutBackgroundPatternColor).SequenceEqual(r.Row.BackgroundRgb)),state=State(r.Material)}).ToArray();
        var result=new{format="ac2r.hatch-audit/1",document=doc.PathName,expected=prepared.Count,verified=rows.Count(r=>r.verified),failed=rows.Count(r=>!r.verified),
            analogues=rows.Count(r=>r.match=="analogue"),empty=rows.Count(r=>r.match=="empty"),solid=rows.Count(r=>r.match=="solid"),materials=rows};
        File.WriteAllText(output,JsonSerializer.Serialize(result,Json.Options));
        if(rows.Any(r=>!r.verified))throw new InvalidDataException("Material cut hatch readback differs");return result;
    }
}
