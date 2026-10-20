using Autodesk.Revit.DB;
using Autodesk.Revit.DB.ExtensibleStorage;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Ac2Revit;

/// <summary>The ribbon and diagnostic runner use the same ordered pipeline.</summary>
public static class PipelineRunner
{
    public sealed class Plan
    {
        public string PipelineFormat {get;set;}="";
        public string SourceManifest {get;set;}="";
        public string Mapping {get;set;}="";
        public string GridsManifest {get;set;}="";
        public string ColumnNativeManifest {get;set;}="";
        public string ColumnNativeMapping {get;set;}="";
        public string ColumnBodies {get;set;}="";
        public string ColumnExtrusions {get;set;}="";
        public string ColumnTemplate {get;set;}="";
        public string ColumnBehavior {get;set;}="";
        public string BeamBodies {get;set;}="";
        public string RectangularBeams {get;set;}="";
        public string BeamFamily {get;set;}="";
        public string VoidTemplate {get;set;}="";
        public string OpeningsPlan {get;set;}="";
        public string WallBodies {get;set;}="";
        public string RoomSeparators {get;set;}="";
        public string HatchMapping {get;set;}="";
        public string HatchRules {get;set;}="";
        public int RoomAuditLimit {get;set;}=20;
        public bool RebuildRooms {get;set;}=true;
        public bool NativeCutBeams {get;set;}=true;
        public bool RequireCompleteSeparatorCoverage {get;set;}=true;
        public bool EnforceSourceSnapshot {get;set;}
        public bool CompleteWallsFromIfc {get;set;}
        public string ProfileColumnMode {get;set;}="auto";
        public string IfcCoordinateFrame {get;set;}="";
        public string SupplementalBodiesPlan {get;set;}="";
        public bool CompleteInitialWallJoins {get;set;}
    }
    public static bool IsPipeline(string path)
    {using var j=JsonDocument.Parse(File.ReadAllText(path));return j.RootElement.S("pipelineFormat")=="ac2r.pipeline/1";}
    public static Report Run(Document doc,string path,string output,int trialLimit=0,Action<string,int,int>? progress=null,bool preview=false,string executionId="",ImportSelection? selection=null)
    {
        var plan=JsonSerializer.Deserialize<Plan>(File.ReadAllText(path),Json.Options)??throw new InvalidDataException("Empty pipeline");
        if(plan.PipelineFormat!="ac2r.pipeline/1")throw new InvalidDataException("Unsupported pipeline format");
        if(plan.ProfileColumnMode is not ("auto" or "native" or "ifc"))throw new InvalidDataException("Invalid profileColumnMode");
        string Resolve(string value)
        {
            if(string.IsNullOrWhiteSpace(value))throw new InvalidDataException("Missing required pipeline input");
            string full=Path.GetFullPath(value,Path.GetDirectoryName(path)!);
            if(!File.Exists(full))throw new FileNotFoundException("Pipeline input missing",full);return full;
        }
        var inputs=new[]{plan.SourceManifest,plan.Mapping,plan.GridsManifest,plan.ColumnNativeManifest,plan.ColumnNativeMapping,
            plan.ColumnBodies,plan.ColumnExtrusions,plan.ColumnTemplate,plan.BeamBodies,plan.RectangularBeams,plan.BeamFamily,plan.VoidTemplate,plan.OpeningsPlan};
        foreach(var input in inputs)Resolve(input);
        if(plan.WallBodies.Length>0)Resolve(plan.WallBodies);
        if(plan.CompleteWallsFromIfc && plan.WallBodies.Length>0)throw new InvalidDataException("Choose fixed wall bodies or missing-wall extraction, not both");
        if(plan.ColumnBehavior.Length>0)Resolve(plan.ColumnBehavior);
        if(plan.IfcCoordinateFrame.Length>0)Resolve(plan.IfcCoordinateFrame);
        if(plan.SupplementalBodiesPlan.Length>0)Resolve(plan.SupplementalBodiesPlan);
        if(plan.RoomSeparators.Length>0)Resolve(plan.RoomSeparators);
        if(plan.HatchMapping.Length>0)Resolve(plan.HatchMapping);
        if(plan.HatchRules.Length>0)Resolve(plan.HatchRules);
        if(plan.HatchMapping.Length>0 && plan.HatchRules.Length>0)throw new InvalidDataException("Choose a fixed hatch mapping or dynamic hatch rules, not both");
        string hatchMappingPath=plan.HatchMapping.Length>0?Resolve(plan.HatchMapping):"";
        var manifest=Manifest.Load(Resolve(plan.SourceManifest));string project=manifest.Source.ProjectId;
        var wallKeys=manifest.Elements.Where(e=>e.Kind=="Wall").Select(e=>e.SourceGuid).ToHashSet(StringComparer.OrdinalIgnoreCase);
        Dictionary<string,long> WallIdentities()=>Storage.Index(doc,project).Where(p=>wallKeys.Contains(p.Key) && (p.Value is Wall or DirectShape)).ToDictionary(p=>p.Key,p=>p.Value.Id.Value,StringComparer.OrdinalIgnoreCase);
        var wallsBefore=WallIdentities();
        var config=ImportConfig.Load(Resolve(plan.Mapping));var report=new Report{ProjectId=project,Mode=preview?"pipelinePreview":"pipeline"};
        string[] order=["resources","prepareHatchMapping","hatchMapping","grids","simpleColumns","columnBodies","profileColumns","columnCompletion","columnBehavior","wallsSlabsMorphs","wallCompletion","beamBodies","rectangularBeams","nativeCutBeams","openingsAndFillings","supplementalBodies","roomSeparators","wallJoinsAndRooms","roomBoundaryAudit","views","separatorAudit","hatchAudit","sourceCoverage"];
        if(preview){foreach(var stage in order)report.Add(stage,"PipelineStage","planned");return report;}
        if(plan.EnforceSourceSnapshot)
        {
            using var sourceStream=File.OpenRead(Resolve(plan.SourceManifest));
            string manifestSha=Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(sourceStream));
            using var body=JsonDocument.Parse(File.ReadAllText(Resolve(plan.BeamBodies)));
            string referenceSha=body.RootElement.S("ifcSha256");
            if(body.RootElement.S("projectId")!=project || referenceSha.Length!=64)throw new InvalidDataException("Different session reference project");
            VerifiedReferenceScope.Verify(Resolve(body.RootElement.S("ifcPath")),referenceSha);
            string frameSha="";
            if(plan.IfcCoordinateFrame.Length>0)
            {
                using var frame=JsonDocument.Parse(File.ReadAllText(Resolve(plan.IfcCoordinateFrame)));
                if(frame.RootElement.S("projectId")!=project || frame.RootElement.S("format")!="ac2r.ifc-frame/1")throw new InvalidDataException("IFC coordinate frame project mismatch");
                string frameHash=frame.RootElement.S("hash");
                foreach(string input in new[]{plan.ColumnBodies,plan.BeamBodies,plan.OpeningsPlan})
                {
                    using var data=JsonDocument.Parse(File.ReadAllText(Resolve(input)));
                    if(!data.RootElement.TryGetProperty("ifcToSource",out var coordinates) || coordinates.S("hash")!=frameHash)
                        throw new InvalidDataException("Mixed IFC coordinate frames: "+input);
                }
                using var frameStream=File.OpenRead(Resolve(plan.IfcCoordinateFrame));
                frameSha=Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(frameStream));
            }
            string hash=frameSha.Length==0?Json.Hash(new{manifestSha,referenceSha}):Json.Hash(new{manifestSha,referenceSha,frameSha});const string key="pipeline-source-snapshot";
            var index=Storage.Index(doc,project);
            if(index.TryGetValue(key,out var state))
            {
                if(Storage.Read(state)?.Hash!=hash)throw new InvalidDataException("В текущем проекте другой снимок Archicad/IFC. Соберите новый экспорт в новом проекте Revit. Обновление изменённой геометрии ещё не поддерживается.");
            }
            else
            {
                if(manifest.Elements.Any(e=>index.ContainsKey(e.SourceGuid)))throw new InvalidDataException("В текущем проекте уже есть импорт Archicad без подтверждённого снимка этого сеанса. Выберите сборку в новом проекте Revit.");
                using var t=new Transaction(doc,"Register Archicad exchange source snapshot");t.Start();
                var storage=DataStorage.Create(doc);Storage.Tag(storage,project,key,hash,JsonSerializer.Serialize(new{manifestSha,referenceSha,frameSha},Json.Options));t.Commit();
            }
            report.Add(key,"SourceSnapshot","verified","Exact JSON/IFC snapshot guard; changed source requires a new project.");
        }
        Directory.CreateDirectory(output);int stageNumber=0;
        if(selection!=null)File.WriteAllText(Path.Combine(output,"import-selection.json"),JsonSerializer.Serialize(new{requested=selection.Requested.Order(),included=selection.Included.Order(),dependencies=selection.Included.Except(selection.Requested).Order()},Json.Options));
        void Stage(string name,Func<Report> action)
        {
            if(selection!=null && !selection.AllowsStage(name)){report.Add(name,"PipelineStage","notSelected","Этап не выбран пользователем.");return;}
            progress?.Invoke(name,stageNumber++,order.Length);
            // Cache only physical stages, and only within this execution's
            // automatic reopen cycle. A later user invocation always rechecks
            // existing elements against the full source snapshot.
            bool cache=executionId.Length>0 && name is "grids" or "simpleColumns" or "columnBodies" or "profileColumns" or "columnCompletion" or "columnBehavior" or "wallsSlabsMorphs" or "wallCompletion" or "beamBodies" or "rectangularBeams" or "nativeCutBeams" or "openingsAndFillings" or "supplementalBodies" or "roomSeparators";
            string key="pipeline-stage:"+name;
            var saved=cache?Storage.Index(doc,project).GetValueOrDefault(key) as DataStorage:null;
            bool reused=saved!=null && Storage.Read(saved)?.Hash==executionId;
            Report result;
            if(reused)
            {
                var schema=Storage.Schema();result=JsonSerializer.Deserialize<Report>(saved!.GetEntity(schema).Get<string>(schema.GetField("RawJson")),Json.Options)
                    ??throw new InvalidDataException("Invalid completed stage checkpoint: "+name);
            }
            else
            {
                result=action();
                if(cache)
                {
                    using var t=new Transaction(doc,"Record completed import stage "+name);t.Start();
                    var state=saved??DataStorage.Create(doc);Storage.Tag(state,project,key,executionId,JsonSerializer.Serialize(result,Json.Options));t.Commit();
                }
            }
            result.Save(Path.Combine(output,name+".json"));report.Items.AddRange(result.Items);report.ZoneAreas.AddRange(result.ZoneAreas);report.WallJoins.AddRange(result.WallJoins);
            report.Save(Path.Combine(output,"pipeline-report.json"));
            if(!reused && !string.IsNullOrEmpty(doc.PathName))doc.Save();
        }
        Report ImportMissing(string file,ISet<string> kinds,ImportConfig options,bool resources=false)
        {
            if(selection!=null)kinds=kinds.Where(selection.Includes).ToHashSet();
            if(kinds.Count==0)return new Report{ProjectId=project,Mode="notSelected"};
            var m=Manifest.Load(file);var index=Storage.Index(doc,project);
            if(m.Source.ProjectId!=project)throw new InvalidDataException("Pipeline project mismatch");
            // Dependencies remain defined in the full source contract; retain all
            // source walls for topology and use the selector only at execution.
            var selected=m.Elements.Where(e=>kinds.Contains(e.Kind) && !index.ContainsKey(e.SourceGuid)).ToList();
            foreach(var e in selected.Where(e=>e.Kind=="Grid"))
            {
                var p=JsonNode.Parse(e.Payload.GetRawText())!.AsObject();string name=p["name"]!.GetValue<string>();
                p["name"]=new string(name.Select(c=>"\\:{}[]|;<>?`~".Contains(c)||char.IsControl(c)?'_':c).ToArray());
                e.Payload=JsonSerializer.SerializeToElement(p);
            }
            // Run sees only missing keys of selected kinds; source dependencies
            // are hydrated from storage when their host already exists.
            var original=m.Elements;var combined=new Report{ProjectId=project,Mode="pipelineElementBatches"};int processed=0;
            foreach(var batch in selected.Chunk(200))
            {
                m.Elements=original.Where(e=>!kinds.Contains(e.Kind) || batch.Contains(e)).ToList();int offset=processed;
                var importer=new Importer(doc,m,options){Progress=(phase,n,total)=>progress?.Invoke(phase,offset+n,selected.Count)};
                var result=importer.Run(kinds,resources && processed==0,false);combined.Items.AddRange(result.Items);processed+=batch.Length;
                combined.Save(Path.Combine(output,string.Join("-",kinds.OrderBy(k=>k))+"-checkpoint.json"));
                if(!string.IsNullOrEmpty(doc.PathName))doc.Save();
            }
            return combined;
        }
        string MissingPlan(string file,string array,string name,bool meshesOnly=false)
        {
            var node=JsonNode.Parse(File.ReadAllText(file))!.AsObject();var index=Storage.Index(doc,project);var rows=node[array]!.AsArray();
            var keep=rows.Where(r=>r!=null && (meshesOnly?index.GetValueOrDefault(r!["sourceGuid"]!.GetValue<string>()) is DirectShape:!index.ContainsKey(r!["sourceGuid"]!.GetValue<string>()))).Select(r=>r!.DeepClone()).ToList();
            node[array]=new JsonArray(keep.ToArray());string target=Path.Combine(output,name+"-input.json");File.WriteAllText(target,node.ToJsonString());return target;
        }
        int Count(string file,string array){using var j=JsonDocument.Parse(File.ReadAllText(file));return j.RootElement.GetProperty(array).GetArrayLength();}
        Stage("resources",()=>
        {
            var originalTypes=manifest.Types;Report r;
            try
            {
                if(selection!=null)manifest.Types=originalTypes.Where(t=>selection.IncludesType(t.Kind)).ToList();
                r=new Importer(doc,manifest,config).Run(new HashSet<string>(),true,false);
            }
            finally{manifest.Types=originalTypes;}
            var visual=Manifest.Load(Resolve(plan.GridsManifest));if(visual.Surfaces.Count>0)MaterialGraphics.Apply(doc,visual,r);return r;
        });
        if(plan.HatchRules.Length>0)Stage("prepareHatchMapping",()=>
        {
            var result=new Report{ProjectId=project,Mode="dynamicHatchMapping"};
            string folder=Path.Combine(output,"hatches");Directory.CreateDirectory(folder);
            string inventory=Path.Combine(folder,"revit-catalog.json");HatchMappingStage.Inventory(doc,manifest,inventory);
            try
            {
                ExchangeServices.Run("prepare_hatch_mapping.py",["--manifest",Resolve(plan.SourceManifest),"--target",inventory,
                    "--rules",Resolve(plan.HatchRules),"--out",folder,"--allow-unmapped"],line=>File.AppendAllText(Path.Combine(folder,"prepare.log"),line+Environment.NewLine));
                hatchMappingPath=Path.Combine(folder,"hatch-mapping.json");
                using var summary=JsonDocument.Parse(File.ReadAllText(Path.Combine(folder,"summary.json")));var s=summary.RootElement;
                result.Add("hatch-coverage","HatchMapping",s.D("unmapped")==0?"verified":"partial",$"Mapped {s.D("mapped")}; unmapped {s.D("unmapped")}; see hatches/mapping-inventory.csv. No guessed replacements.");
            }
            catch(Exception ex){result.Add("hatch-prepare","HatchMapping","partial","Штриховки не применены: "+ex.Message);}
            return result;
        });
        if(hatchMappingPath.Length>0)Stage("hatchMapping",()=>HatchMappingStage.Run(doc,manifest,Resolve(plan.SourceManifest),hatchMappingPath,Path.Combine(output,"hatch-initial-audit.json")));
        Stage("grids",()=>ImportMissing(Resolve(plan.GridsManifest),new HashSet<string>{"Grid"},config));
        Stage("gridLabels",()=>GridLabelStage.Run(doc,Manifest.Load(Resolve(plan.GridsManifest))));
        Stage("simpleColumns",()=>ImportMissing(Resolve(plan.ColumnNativeManifest),new HashSet<string>{"Column"},ImportConfig.Load(Resolve(plan.ColumnNativeMapping))));
        Stage("columnBodies",()=>
        {
            string input=MissingPlan(Resolve(plan.ColumnBodies),"elements","column-bodies");
            return ReferenceFallback.Run(doc,input,Path.GetFullPath,(n,total)=>progress?.Invoke("columnBodies",n,total),BuiltInCategory.OST_StructuralColumns,"ColumnBody");
        });
        Stage("profileColumns",()=>
        {
            string input=MissingPlan(Resolve(plan.ColumnExtrusions),"columns","profile-columns",true);
            // Large native profile replacement crashed Revit's constraint solver
            // during a large import after private memory grew to 23 GB. Keep the already
            // verified IFC bodies rather than risking the complete import.
            if(plan.ProfileColumnMode=="ifc" || (plan.ProfileColumnMode=="auto" && Count(input,"columns")>500))
            {
                var retained=new Report{ProjectId=project,Mode="verifiedIfcColumns"};
                using var source=JsonDocument.Parse(File.ReadAllText(input));var index=Storage.Index(doc,project);
                foreach(var row in source.RootElement.GetProperty("columns").EnumerateArray())
                {
                    string key=row.S("sourceGuid");
                    if(index.GetValueOrDefault(key) is not DirectShape shape)throw new InvalidDataException("Verified source column body missing: "+key);
                    retained.Add(key,"Column","retained","Точная IFC-геометрия сохранена. Нативная профильная замена отложена: защита от сбоя решателя ограничений на массовой обработке. Параметрическое редактирование профиля недоступно.",shape.UniqueId);
                }
                return retained;
            }
            return ExtrudedColumnReplacer.Run(doc,input,Resolve(plan.ColumnTemplate),Path.Combine(output,"column-families"),progress);
        });
        Stage("columnCompletion",()=>NominalColumnStage.Run(doc,manifest,ImportConfig.Load(Resolve(plan.ColumnNativeMapping)),Path.Combine(output,"nominal-columns"),progress));
        if(plan.ColumnBehavior.Length>0)Stage("columnBehavior",()=>ColumnBehaviorStage.Run(doc,Resolve(plan.ColumnBehavior),project,Path.Combine(output,"column-behavior")));
        Stage("wallsSlabsMorphs",()=>ImportMissing(Resolve(plan.SourceManifest),new HashSet<string>{"Wall","Slab","Morph"},config));
        Stage("wallCompletion",()=>
        {
            string? bodyPath=plan.WallBodies.Length>0?Resolve(plan.WallBodies):null;
            if(plan.CompleteWallsFromIfc)
            {
                var existing=Storage.Index(doc,project);
                var missing=manifest.Elements.Where(e=>e.Kind=="Wall" && !existing.ContainsKey(e.SourceGuid)).Select(e=>e.SourceGuid).ToArray();
                if(missing.Length>0)
                {
                    string keys=Path.Combine(output,"missing-wall-keys.json");File.WriteAllText(keys,JsonSerializer.Serialize(missing,Json.Options));
                    using var reference=JsonDocument.Parse(File.ReadAllText(Resolve(plan.BeamBodies)));
                    string folder=Path.Combine(output,"missing-wall-ifc");
                    progress?.Invoke("wallGeometryPreparation",0,missing.Length);
                    ExchangeServices.Run("prepare_column_bodies.py",["--manifest",Resolve(plan.SourceManifest),"--ifc",Resolve(reference.RootElement.S("ifcPath")),
                        "--kind","Wall","--keys-file",keys,"--out",folder],line=>File.AppendAllText(Path.Combine(output,"missing-wall-ifc.log"),line+Environment.NewLine));
                    bodyPath=Path.Combine(folder,"wall-bodies.json");
                }
            }
            return WallCompletion.Run(doc,manifest,bodyPath,Path.Combine(output,"wall-completion"),progress);
        });
        Stage("beamBodies",()=>
        {
            string input=MissingPlan(Resolve(plan.BeamBodies),"elements","beam-bodies");
            return ReferenceFallback.Run(doc,input,Path.GetFullPath,(n,total)=>progress?.Invoke("beamBodies",n,total),BuiltInCategory.OST_StructuralFraming,"BeamBody");
        });
        Stage("rectangularBeams",()=>
        {
            string input=MissingPlan(Resolve(plan.RectangularBeams),"beams","rectangular-beams",true);
            return Count(input,"beams")==0?new Report{ProjectId=project}:RectangularBeamReplacer.Run(doc,input,Resolve(plan.BeamFamily),(n,total)=>progress?.Invoke("rectangularBeams",n,total));
        });
        if(plan.NativeCutBeams)Stage("nativeCutBeams",()=>CutBeamReplacer.Run(doc,Resolve(plan.BeamBodies),Resolve(plan.BeamFamily),Resolve(plan.VoidTemplate),Path.Combine(output,"beam-void-families"),trialLimit,(n,total)=>
        {progress?.Invoke("nativeCutBeams",n,total);if(n%20==0 && !string.IsNullOrEmpty(doc.PathName))doc.Save();}));
        Stage("openingsAndFillings",()=>OpeningStage.Run(doc,Resolve(plan.OpeningsPlan),Resolve(plan.VoidTemplate),Path.Combine(output,"opening-families"),trialLimit,progress,selection?.Included));
        if(plan.SupplementalBodiesPlan.Length>0)Stage("supplementalBodies",()=>SupplementalBodiesStage.Run(doc,Resolve(plan.SupplementalBodiesPlan),Path.Combine(output,"supplemental-inputs"),progress,selection?.Included));
        if(plan.RoomSeparators.Length>0)Stage("roomSeparators",()=>RoomSeparatorStage.Run(doc,manifest,Resolve(plan.SourceManifest),Resolve(plan.RoomSeparators),Path.Combine(output,"room-separators"),progress));
        // Spatial elements are deliberately rebuilt only after every physical
        // stage above has returned, including all native cuts and wall fillings.
        if(plan.RebuildRooms && trialLimit==0)
        {
            Report? spatial=null;
            Stage("wallJoinsAndRooms",()=>
            {
                var wallsAfter=WallIdentities();
                bool changed=wallsBefore.Count!=wallsAfter.Count || wallsBefore.Any(p=>!wallsAfter.TryGetValue(p.Key,out long id) || id!=p.Value);
                const string joinState="pipeline-wall-joins-completed";
                bool firstJoin=plan.CompleteInitialWallJoins && !Storage.Index(doc,project).ContainsKey(joinState);
                bool connect=config.ConnectWalls;
                try
                {
                    // Reapplying native end joins is not a read-only validation:
                    // Revit can choose a different cleanup even with identical axes.
                    // ImportMissing never edits existing walls. When their source
                    // identities are unchanged, preserve the saved join geometry.
                    if(!changed && !firstJoin)config.ConnectWalls=false;
                    var result=new Report{ProjectId=project,Mode="checkpointedSpatial"};
                    if(config.ConnectWalls)
                    {
                        int lastSaved=0;
                        var joins=new Importer(doc,manifest,config){Progress=(phase,n,total)=>
                        {
                            progress?.Invoke(phase,n,total);
                            if(phase=="wallEndJoins" && n-lastSaved>=512 && !string.IsNullOrEmpty(doc.PathName))
                            {doc.Save();lastSaved=n;}
                        }}.JoinSourceWalls();
                        joins.Save(Path.Combine(Path.GetDirectoryName(doc.PathName)!,"wall-join-checkpoint-report.json"));
                        result.Items.AddRange(joins.Items);result.WallJoins.AddRange(joins.WallJoins);
                    }
                    if(connect && !changed && !firstJoin)result.Add("wall-topology","WallJoin","retained","No source wall instances created or replaced. Existing joins preserved; no new junction verification claimed.");
                    if(connect && (changed || firstJoin))
                    {
                        using var stateTransaction=new Transaction(doc,"Record completed source wall join pass");stateTransaction.Start();
                        var state=Storage.Index(doc,project).GetValueOrDefault(joinState) as DataStorage??DataStorage.Create(doc);
                        Storage.Tag(state,project,joinState,Json.Hash(wallsAfter),"Wall join pass completed; per-junction acceptance remains in the spatial report.");stateTransaction.Commit();
                        if(!string.IsNullOrEmpty(doc.PathName))doc.Save();
                    }
                    // Joining has its own saved checkpoint. Keep the room-only
                    // configuration identical on the first pass and after reopen.
                    config.ConnectWalls=false;
                    if(selection==null || selection.Includes("Zone"))
                    {
                        var rooms=SpatialStage.Run(doc,manifest,config,Path.Combine(Path.GetDirectoryName(doc.PathName)!,"spatial-checkpoints"),progress);
                        result.Items.AddRange(rooms.Items);result.ZoneAreas.AddRange(rooms.ZoneAreas);
                    }
                    return spatial=result;
                }
                finally{config.ConnectWalls=connect;}
            });
            if(plan.RoomAuditLimit>0 && (selection==null || selection.Includes("Zone")))RoomBoundaryAudit.Write(doc,manifest,spatial!,Path.Combine(output,"largest-room-boundaries.json"),plan.RoomAuditLimit,progress);
        }
        using(var t=new Transaction(doc,"Complete import diagnostic view"))
        {
            t.Start();var view=new FilteredElementCollector(doc).OfClass(typeof(View3D)).Cast<View3D>().FirstOrDefault(v=>v.Name=="AC Complete Model");
            if(view==null)
            {
                var vft=new FilteredElementCollector(doc).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>().First(v=>v.ViewFamily==ViewFamily.ThreeDimensional);
                view=View3D.CreateIsometric(doc,vft.Id);view.Name="AC Complete Model";
                var original=new FilteredElementCollector(doc).OfClass(typeof(View3D)).Cast<View3D>().FirstOrDefault(v=>v.Name=="AC Import Diagnostic");
                if(original!=null)view.SetOrientation(original.GetOrientation());
            }
            view.DisplayStyle=DisplayStyle.Shading;view.DetailLevel=ViewDetailLevel.Fine;t.Commit();
        }
        using(var t=new Transaction(doc,"Imported story plan views"))
        {
            t.Start();var index=Storage.Index(doc,project);
            var planType=new FilteredElementCollector(doc).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>().First(v=>v.ViewFamily==ViewFamily.FloorPlan);
            var names=new FilteredElementCollector(doc).OfClass(typeof(ViewPlan)).Cast<ViewPlan>().Select(v=>v.Name).ToHashSet(StringComparer.OrdinalIgnoreCase);
            foreach(var story in manifest.Levels)
                if(index.GetValueOrDefault(story.Key) is Level level && names.Add("AC Plan "+level.Name))
                {var planView=ViewPlan.Create(doc,planType.Id,level.Id);planView.Name="AC Plan "+level.Name;planView.DetailLevel=ViewDetailLevel.Fine;}
            t.Commit();
        }
        Stage("serviceCuts",()=>ServiceCutsStage.Run(doc,project));
        if(plan.RoomSeparators.Length>0 && (selection==null || selection.Includes("Zone")))
        {
            // Native boundary cleanup can happen when walls join or rooms are
            // evaluated. Preserve the actual curves; remeasure them against the
            // immutable source references, explicitly retaining any deviations.
            Stage("roomSeparatorRecheck",()=>RoomSeparatorStage.Run(doc,manifest,Resolve(plan.SourceManifest),Resolve(plan.RoomSeparators),Path.Combine(output,"room-separator-recheck"),progress));
            var audit=JsonSerializer.SerializeToElement(RoomSeparatorStage.Audit(doc,manifest,Resolve(plan.RoomSeparators),Path.Combine(output,"separator-final-audit.json")),Json.Options);
            int expected=audit.GetProperty("expectedNativeCurves").GetInt32();
            if(audit.GetProperty("missing").GetInt32()!=0 || audit.GetProperty("changed").GetInt32()!=0
                || audit.GetProperty("nativeSnapshotsVerified").GetInt32()!=expected || audit.GetProperty("exactSourceReferencesVerified").GetInt32()!=expected
                || (plan.RequireCompleteSeparatorCoverage && audit.GetProperty("representedSourceGuids").GetInt32()!=audit.GetProperty("sourceElements").GetInt32())
                || audit.GetProperty("extraTaggedKeys").GetArrayLength()!=0)
                throw new InvalidDataException("Final separator/source-reference audit failed after room evaluation; inspect separator-final-audit.json");
            report.Add("separator-final-audit","RoomSeparator","verified",$"{expected} native snapshots and exact source references verified after spatial evaluation; {audit.GetProperty("nativeAdjusted").GetInt32()} native adaptations remain explicitly incomplete.");
            if(audit.GetProperty("representedSourceGuids").GetInt32()!=audit.GetProperty("sourceElements").GetInt32())
                report.Add("separator-source-coverage","RoomSeparator","partial","Some source separator kinds remain unsupported; see source separator plan and stage report.");
        }
        if(hatchMappingPath.Length>0)HatchMappingStage.Audit(doc,manifest,Resolve(plan.SourceManifest),hatchMappingPath,Path.Combine(output,"hatch-final-audit.json"));
        var finalIndex=Storage.Index(doc,project);
        var sourceIssues=report.Items.Where(i=>i.Status is "blocked" or "failed" or "unsupported")
            .GroupBy(i=>i.SourceKey,StringComparer.OrdinalIgnoreCase).ToDictionary(g=>g.Key,g=>string.Join("\n",g.Select(i=>i.Message).Distinct().Take(3)),StringComparer.OrdinalIgnoreCase);
        var coverage=manifest.Elements.Select(e=>new{sourceGuid=e.SourceGuid,kind=e.Kind,sourceStatus=e.Status,selected=selection==null || selection.Includes(e.Kind),
            represented=finalIndex.ContainsKey(e.SourceGuid),representation=finalIndex.GetValueOrDefault(e.SourceGuid)?.GetType().Name??"",
            reason=selection!=null && !selection.Includes(e.Kind)?"Не выбрано пользователем":finalIndex.ContainsKey(e.SourceGuid)?"":sourceIssues.GetValueOrDefault(e.SourceGuid,
                string.IsNullOrWhiteSpace(e.Reason)?"Категория или тип элемента пока не поддержаны этапами полной сборки; исходные данные сохранены в JSON.":e.Reason)}).ToArray();
        File.WriteAllText(Path.Combine(output,"source-coverage.json"),JsonSerializer.Serialize(new{projectId=project,elements=coverage},Json.Options));
        foreach(var rows in coverage.Where(e=>e.selected).GroupBy(e=>e.kind))
            report.Add("coverage:"+rows.Key,"SourceCoverage",rows.All(e=>e.represented)?"verified":"partial",
                $"{rows.Key}: source {rows.Count()}, represented {rows.Count(e=>e.represented)}, missing {rows.Count(e=>!e.represented)}; see source-coverage.json. Geometry and native functionality require separate acceptance.");
        report.Add("pipeline","Pipeline","complete",trialLimit>0?"Control subset; room rebuilding deliberately deferred to complete physical import":"Physical stages completed before spatial evaluation; inspect per-element blocked/retained outcomes.");
        report.Save(Path.Combine(output,"pipeline-report.json"));return report;
    }
}
