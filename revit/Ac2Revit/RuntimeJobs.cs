using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Autodesk.Revit.UI.Events;
using System.Text.Json;

namespace Ac2Revit;

/// <summary>Local diagnostics into new projects; no network listener or arbitrary code execution.</summary>
public static class RuntimeJobs
{
    static string root = "";
    static string inbox = "";
    static string legacyInbox = "";
    static bool busy;
    static ExternalEvent? dispatch;
    static bool queued;
    public static void Start(UIControlledApplication app, string assembly)
    {
        root = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(assembly)!, "..", ".."));
        legacyInbox = Path.Combine(root, "artifacts", "runtime", "job.json");
        inbox = Path.Combine(root, "artifacts", "runtime", "job-v"+typeof(App).Assembly.GetName().Version!.Minor+".json");
        dispatch=ExternalEvent.Create(new Handler());
        app.Idling += OnIdle;
    }
    public static void Stop(UIControlledApplication app) {app.Idling -= OnIdle;dispatch?.Dispose();}
    static string WorkspacePath(string path)
    {
        string full = Path.GetFullPath(path);
        if (!full.StartsWith(root+Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Runtime job path must remain inside the workspace");
        return full;
    }
    static void OnIdle(object? sender, IdlingEventArgs args)
    {
        // An Idling callback cannot switch an already active project. Dispatch
        // the entire operation to a supported ExternalEvent API context instead.
        if(busy || queued || (!File.Exists(inbox) && !File.Exists(legacyInbox)) || dispatch==null)return;
        queued=dispatch.Raise()==ExternalEventRequest.Accepted;
    }
    sealed class Handler:IExternalEventHandler
    {
        public string GetName()=>"Ac2Revit staged diagnostic import";
        public void Execute(UIApplication ui){queued=false;Process(ui);}
    }
    static void Process(UIApplication ui)
    {
        string sourceInbox=File.Exists(inbox)?inbox:legacyInbox;
        if(busy || !File.Exists(sourceInbox))return;
        busy = true;
        string resultPath = Path.Combine(root, "artifacts", "runtime", "job-result.json");
        Document? scratch = null;
        Importer? importer = null;
        var timer = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            var job = JsonSerializer.Deserialize<Job>(File.ReadAllText(sourceInbox), Json.Options) ?? throw new InvalidDataException("Empty runtime job");
            File.Move(sourceInbox, Path.Combine(Path.GetDirectoryName(sourceInbox)!, "job-"+DateTime.UtcNow.ToString("yyyyMMdd-HHmmssfff")+".json"));
            resultPath = WorkspacePath(job.ResultPath);
            if(job.Operation=="modelPreview")
            {
                string source=WorkspacePath(job.SourceRvt),folder=Path.GetDirectoryName(WorkspacePath(job.ReportPath))!;Directory.CreateDirectory(folder);
                var active=ui.ActiveUIDocument;
                if(active==null || !string.Equals(active.Document.PathName,source,StringComparison.OrdinalIgnoreCase))active=ui.OpenAndActivateDocument(source);
                var document=active.Document;var previewManifest=Manifest.Load(WorkspacePath(job.ManifestPath));var index=Storage.Index(document,previewManifest.Source.ProjectId);
                var physical=previewManifest.Elements.Where(e=>e.Kind!="Zone").Select(e=>index.GetValueOrDefault(e.SourceGuid)).Where(e=>e is Wall or Floor or FamilyInstance or DirectShape).Cast<Element>().ToList();
                var boxes=physical.Select(e=>e.get_BoundingBox(null)).Where(b=>b!=null).ToList();
                if(boxes.Count==0)throw new InvalidDataException("No imported physical geometry to preview");
                var lo=new XYZ(boxes.Min(b=>b.Min.X),boxes.Min(b=>b.Min.Y),boxes.Min(b=>b.Min.Z));var hi=new XYZ(boxes.Max(b=>b.Max.X),boxes.Max(b=>b.Max.Y),boxes.Max(b=>b.Max.Z));var center=(lo+hi)*.5;
                var view=new FilteredElementCollector(document).OfClass(typeof(View3D)).Cast<View3D>().First(v=>v.Name=="AC Complete Model");
                using(var t=new Transaction(document,"Imported model presentation"))
                {
                    t.Start();view.DisplayStyle=DisplayStyle.Shading;view.DetailLevel=ViewDetailLevel.Fine;
                    double margin=Geometry.Feet(5);view.SetSectionBox(new BoundingBoxXYZ{Min=lo-new XYZ(margin,margin,margin),Max=hi+new XYZ(margin,margin,margin)});view.IsSectionBoxActive=true;
                    var direction=new XYZ(-1,1,-.7).Normalize();var up=(XYZ.BasisZ-direction*XYZ.BasisZ.DotProduct(direction)).Normalize();
                    view.SetOrientation(new ViewOrientation3D(center-direction*Math.Max(hi.DistanceTo(lo),100),up,direction));t.Commit();
                }
                active.ActiveView=view;active.GetOpenUIViews().FirstOrDefault(v=>v.ViewId==view.Id)?.ZoomToFit();
                var options=new ImageExportOptions{ExportRange=ExportRange.SetOfViews,FilePath=Path.Combine(folder,"Ippodrom_model"),HLRandWFViewsFileType=ImageFileType.PNG,ShadowViewsFileType=ImageFileType.PNG,PixelSize=2000,ZoomType=ZoomFitType.FitToPage,ImageResolution=ImageResolution.DPI_150};options.SetViewsAndSheets(new List<ElementId>{view.Id});document.ExportImage(options);document.Save();
                var coverage=previewManifest.Elements.GroupBy(e=>e.Kind).ToDictionary(g=>g.Key,g=>new{source=g.Count(),represented=g.Count(e=>index.ContainsKey(e.SourceGuid)),missing=g.Count(e=>!index.ContainsKey(e.SourceGuid)),classes=g.Where(e=>index.ContainsKey(e.SourceGuid)).GroupBy(e=>index[e.SourceGuid].GetType().Name).ToDictionary(c=>c.Key,c=>c.Count())});
                var result=new{status="complete",document=source,coverage,physicalElements=physical.Count,boundsMinMeters=new[]{lo.X,lo.Y,lo.Z}.Select(v=>UnitUtils.ConvertFromInternalUnits(v,UnitTypeId.Meters)),boundsMaxMeters=new[]{hi.X,hi.Y,hi.Z}.Select(v=>UnitUtils.ConvertFromInternalUnits(v,UnitTypeId.Meters)),images=Directory.GetFiles(folder,"Ippodrom_model*.png")};
                File.WriteAllText(WorkspacePath(job.ReportPath),JsonSerializer.Serialize(result,Json.Options));File.WriteAllText(resultPath,JsonSerializer.Serialize(result,Json.Options));return;
            }
            if(job.Operation=="supplementalBodiesCopy")
            {
                var bundle=System.Text.Json.Nodes.JsonNode.Parse(File.ReadAllText(WorkspacePath(job.ManifestPath)))!;
                if(bundle["format"]?.GetValue<string>()!="ac2r.supplemental/1")throw new InvalidDataException("Invalid supplemental body plan");
                var supplementManifest=Manifest.Load(WorkspacePath(bundle["sourceManifest"]!.GetValue<string>()));string project=supplementManifest.Source.ProjectId;
                if(bundle["projectId"]!.GetValue<string>()!=project)throw new InvalidDataException("Supplemental project mismatch");
                string output=WorkspacePath(job.OutputRvt);if(File.Exists(output))throw new IOException("Supplemental output already exists");
                File.Copy(WorkspacePath(job.SourceRvt),output,false);var document=ui.OpenAndActivateDocument(output).Document;
                string folder=Path.GetDirectoryName(output)!;
                var supplementReport=SupplementalBodiesStage.Run(document,WorkspacePath(job.ManifestPath),Path.Combine(folder,"supplemental-inputs"),
                    (phase,done,total)=>File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status="running",phase,done,total,seconds=timer.Elapsed.TotalSeconds},Json.Options)));
                var finalIndex=Storage.Index(document,project);
                var coverage=supplementManifest.Elements.Select(e=>new{sourceGuid=e.SourceGuid,kind=e.Kind,represented=finalIndex.ContainsKey(e.SourceGuid),representation=finalIndex.GetValueOrDefault(e.SourceGuid)?.GetType().Name??""}).ToArray();
                File.WriteAllText(Path.Combine(folder,"source-coverage.json"),JsonSerializer.Serialize(new{projectId=project,elements=coverage},Json.Options));
                supplementReport.Save(WorkspacePath(job.ReportPath));document.Save();
                var view=new FilteredElementCollector(document).OfClass(typeof(View3D)).Cast<View3D>().First(v=>v.Name=="AC Complete Model");ui.ActiveUIDocument.RequestViewChange(view);
                File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status="complete",output,seconds=timer.Elapsed.TotalSeconds,outcomes=supplementReport.Items.GroupBy(i=>i.Status).ToDictionary(g=>g.Key,g=>g.Count()),coverage=coverage.GroupBy(e=>e.kind).ToDictionary(g=>g.Key,g=>new{source=g.Count(),represented=g.Count(e=>e.represented)})},Json.Options));return;
            }
            if(job.Operation is "hatchInventory" or "hatchCopy" or "hatchAudit")
            {
                var hatchManifest=Manifest.Load(WorkspacePath(job.MeshManifestPath));string source=WorkspacePath(job.SourceRvt);
                if(job.Operation=="hatchCopy")
                {
                    string output=WorkspacePath(job.OutputRvt);if(File.Exists(output))throw new IOException("Hatch output already exists");
                    File.Copy(source,output,false);source=output;
                }
                var active=ui.ActiveUIDocument;
                if(active==null || !string.Equals(active.Document.PathName,source,StringComparison.OrdinalIgnoreCase))active=ui.OpenAndActivateDocument(source);
                object result;
                if(job.Operation=="hatchInventory")result=HatchMappingStage.Inventory(active.Document,hatchManifest,WorkspacePath(job.ReportPath));
                else if(job.Operation=="hatchAudit")result=HatchMappingStage.Audit(active.Document,hatchManifest,WorkspacePath(job.MeshManifestPath),WorkspacePath(job.ManifestPath),WorkspacePath(job.ReportPath));
                else
                {
                    var hatchReport=HatchMappingStage.Run(active.Document,hatchManifest,WorkspacePath(job.MeshManifestPath),WorkspacePath(job.ManifestPath),WorkspacePath(job.ReportPath));
                    hatchReport.Save(Path.ChangeExtension(WorkspacePath(job.ReportPath),"stage.json"));active.Document.Save();
                    result=new{output=source,materials=hatchReport.Items.Count};
                }
                File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status="complete",seconds=timer.Elapsed.TotalSeconds,result},Json.Options));return;
            }
            if(job.Operation is "roomSeparatorAudit" or "roomSeparatorsCheckCopy" or "roomSeparatorsAndRoomsCopy")
            {
                var separatorManifest=Manifest.Load(WorkspacePath(job.MeshManifestPath));
                string source=WorkspacePath(job.SourceRvt);
                if(job.Operation is "roomSeparatorsCheckCopy" or "roomSeparatorsAndRoomsCopy")
                {
                    string output=WorkspacePath(job.OutputRvt);
                    if(File.Exists(output))throw new IOException("Separator check copy already exists");
                    File.Copy(source,output,false);source=output;
                }
                var active=ui.ActiveUIDocument;
                if(active==null || !string.Equals(active.Document.PathName,source,StringComparison.OrdinalIgnoreCase))active=ui.OpenAndActivateDocument(source);
                if(job.Operation is "roomSeparatorsCheckCopy" or "roomSeparatorsAndRoomsCopy")
                {
                    var separatorReport=RoomSeparatorStage.Run(active.Document,separatorManifest,WorkspacePath(job.MeshManifestPath),WorkspacePath(job.ManifestPath),Path.Combine(Path.GetDirectoryName(source)!,"separator-stage"));
                    separatorReport.Save(Path.ChangeExtension(WorkspacePath(job.ReportPath),"stage.json"));active.Document.Save();
                    if(job.Operation=="roomSeparatorsAndRoomsCopy")
                    {
                        var options=ImportConfig.Load(WorkspacePath(job.MappingPath));options.ConnectWalls=false;
                        var spatial=new Importer(active.Document,separatorManifest,options).RebuildSpatialElements();
                        spatial.Save(Path.Combine(Path.GetDirectoryName(source)!,"spatial-report.json"));
                        RoomBoundaryAudit.Write(active.Document,separatorManifest,spatial,Path.Combine(Path.GetDirectoryName(source)!,"largest-room-boundaries.json"));
                        active.Document.Save();
                    }
                }
                RoomSeparatorStage.Audit(active.Document,separatorManifest,WorkspacePath(job.ManifestPath),WorkspacePath(job.ReportPath));
                File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status="complete",source,seconds=timer.Elapsed.TotalSeconds,audit=job.ReportPath},Json.Options));return;
            }
            if(job.Operation=="pipelineAudit")
            {
                var active=ui.ActiveUIDocument;
                if(active==null || !string.Equals(active.Document.PathName,WorkspacePath(job.SourceRvt),StringComparison.OrdinalIgnoreCase))active=ui.OpenAndActivateDocument(WorkspacePath(job.SourceRvt));
                var audit=PipelineAudit.Write(active.Document,WorkspacePath(job.ManifestPath),WorkspacePath(job.ReportPath),(phase,done,total)=>
                    File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status="running",phase,done,total,seconds=timer.Elapsed.TotalSeconds},Json.Options)));
                File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status="complete",audit,seconds=timer.Elapsed.TotalSeconds},Json.Options));return;
            }
            if(job.Operation=="pipelineCopy")
            {
                string output=WorkspacePath(job.OutputRvt);
                if(File.Exists(output))throw new IOException("Pipeline output already exists");
                if(string.IsNullOrEmpty(job.SourceRvt))
                {
                    var created=ui.Application.NewProjectDocument(WorkspacePath(job.TemplatePath));created.SaveAs(output,new SaveAsOptions{OverwriteExistingFile=false,MaximumBackups=2});created.Close(false);
                }
                else File.Copy(WorkspacePath(job.SourceRvt),output,false);
                var document=ui.OpenAndActivateDocument(output).Document;
                var pipelineReport=PipelineExecution.Run(ui,WorkspacePath(job.ManifestPath),Path.Combine(Path.GetDirectoryName(output)!,"stages"),job.Limit,
                    (phase,done,total)=>File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status="running",phase,done,total,seconds=timer.Elapsed.TotalSeconds},Json.Options)));
                document=ui.ActiveUIDocument.Document;pipelineReport.Save(WorkspacePath(job.ReportPath));document.Save();
                var view=new FilteredElementCollector(document).OfClass(typeof(View3D)).Cast<View3D>().First(v=>v.Name=="AC Complete Model");ui.ActiveUIDocument.RequestViewChange(view);
                int Count(BuiltInCategory c)=>new FilteredElementCollector(document).OfCategory(c).WhereElementIsNotElementType().GetElementCount();
                File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status=pipelineReport.Items.Any(r=>r.Status is "blocked" or "failed" or "retained" or "revitError")?"completeWithIssues":"complete",output,seconds=timer.Elapsed.TotalSeconds,
                    outcomes=pipelineReport.Items.GroupBy(r=>r.Status).ToDictionary(g=>g.Key,g=>g.Count()),grids=new FilteredElementCollector(document).OfClass(typeof(Grid)).GetElementCount(),
                    columns=Count(BuiltInCategory.OST_StructuralColumns),beams=Count(BuiltInCategory.OST_StructuralFraming),doors=Count(BuiltInCategory.OST_Doors),windows=Count(BuiltInCategory.OST_Windows),rooms=Count(BuiltInCategory.OST_Rooms)},Json.Options));return;
            }
            if(job.Operation=="nativeRectangularBeamsCopy")
            {
                string source=WorkspacePath(job.SourceRvt),output=WorkspacePath(job.OutputRvt);
                if(File.Exists(output))throw new IOException("Beam output already exists");
                File.Copy(source,output,false);var document=ui.OpenAndActivateDocument(output).Document;
                var beamReport=RectangularBeamReplacer.Run(document,WorkspacePath(job.ManifestPath),WorkspacePath(job.TemplatePath),(done,total)=>
                    File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status="running",phase="nativeBeams",done,total},Json.Options)));
                beamReport.Save(WorkspacePath(job.ReportPath));document.Save();
                var view=new FilteredElementCollector(document).OfClass(typeof(View3D)).Cast<View3D>().FirstOrDefault(v=>v.Name=="AC Beams Check");
                if(view!=null)ui.ActiveUIDocument.RequestViewChange(view);
                File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status=beamReport.Items.Any(r=>r.Status is "retained" or "revitError")?"completeWithRetained":"complete",output,seconds=timer.Elapsed.TotalSeconds,
                    outcomes=beamReport.Items.GroupBy(r=>r.Status).ToDictionary(g=>g.Key,g=>g.Count()),
                    beams=new FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_StructuralFraming).WhereElementIsNotElementType().GetElementCount(),
                    nativeBeams=new FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_StructuralFraming).OfClass(typeof(FamilyInstance)).GetElementCount(),
                    beamDirectShapes=new FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_StructuralFraming).OfClass(typeof(DirectShape)).GetElementCount(),
                    columns=new FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_StructuralColumns).WhereElementIsNotElementType().GetElementCount()},Json.Options));return;
            }
            if(job.Operation=="nativeExtrudedColumnsCopy")
            {
                string source=WorkspacePath(job.SourceRvt),output=WorkspacePath(job.OutputRvt);
                if(File.Exists(output))throw new IOException("Replacement output already exists");
                File.Copy(source,output,false);var document=ui.OpenAndActivateDocument(output).Document;
                var columnReport=ExtrudedColumnReplacer.Run(document,WorkspacePath(job.ManifestPath),WorkspacePath(job.TemplatePath),Path.Combine(Path.GetDirectoryName(output)!,"families"),
                    (phase,done,total)=>File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status="running",phase,done,total,seconds=timer.Elapsed.TotalSeconds},Json.Options)));
                columnReport.Save(WorkspacePath(job.ReportPath));document.Save();
                var view=new FilteredElementCollector(document).OfClass(typeof(View3D)).Cast<View3D>().FirstOrDefault(v=>v.Name=="AC Columns Check");
                if(view!=null)ui.ActiveUIDocument.RequestViewChange(view);
                File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status=columnReport.Items.Any(r=>r.Status is "retained" or "blocked" or "revitError")?"completeWithRetained":"complete",output,seconds=timer.Elapsed.TotalSeconds,
                    outcomes=columnReport.Items.GroupBy(r=>r.Status).ToDictionary(g=>g.Key,g=>g.Count()),
                    columns=new FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_StructuralColumns).WhereElementIsNotElementType().GetElementCount(),
                    nativeColumnInstances=new FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_StructuralColumns).OfClass(typeof(FamilyInstance)).GetElementCount(),
                    columnDirectShapes=new FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_StructuralColumns).OfClass(typeof(DirectShape)).GetElementCount()},Json.Options));return;
            }
            if(job.Operation=="stageImportCopy")
            {
                var source=WorkspacePath(job.SourceRvt);var output=WorkspacePath(job.OutputRvt);
                if(File.Exists(output))throw new IOException("Stage output already exists");
                var staged=Manifest.Load(WorkspacePath(job.ManifestPath));
                if(staged.Elements.Any(e=>e.Kind is not ("Column" or "Grid")))throw new InvalidDataException("This stage accepts only columns and grids");
                if(!string.IsNullOrEmpty(job.ReferenceIFCPath))
                {
                    using var stream=File.OpenRead(WorkspacePath(job.ReferenceIFCPath));
                    string actual=Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(stream));
                    if(!actual.Equals(job.ReferenceIFCSha256,StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("Stage reference IFC changed");
                }
                File.Copy(source,output,false);var document=ui.OpenAndActivateDocument(output).Document;
                var stagedConfig=ImportConfig.Load(Path.Combine(Path.GetDirectoryName(job.ManifestPath)!,"mapping.json"));
                importer=new Importer(document,staged,stagedConfig);
                importer.Progress=(phase,done,total)=>File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status="running",phase,done,total,seconds=timer.Elapsed.TotalSeconds},Json.Options));
                var stageReport=importer.Run();
                if(staged.Surfaces.Count>0)MaterialGraphics.Apply(document,staged,stageReport);
                stageReport.Save(WorkspacePath(job.ReportPath));
                using(var t=new Transaction(document,"Stage diagnostic shading"))
                {
                    t.Start();var view=new FilteredElementCollector(document).OfClass(typeof(View3D)).Cast<View3D>().FirstOrDefault(v=>v.Name=="AC Import Diagnostic");
                    if(view!=null)view.DisplayStyle=DisplayStyle.Shading;t.Commit();
                }
                document.Save();
                var outcomes=stageReport.Items.GroupBy(i=>i.Status).ToDictionary(g=>g.Key,g=>g.Count());
                string status=stageReport.Items.Any(i=>i.Status is "blocked" or "failed" or "conflict")?"completeWithErrors":"complete";
                File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status,output,seconds=timer.Elapsed.TotalSeconds,outcomes,
                    byKind=stageReport.Items.GroupBy(i=>i.Kind).ToDictionary(g=>g.Key,g=>g.GroupBy(i=>i.Status).ToDictionary(s=>s.Key,s=>s.Count())),
                    columns=new FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_StructuralColumns).WhereElementIsNotElementType().GetElementCount(),
                    grids=new FilteredElementCollector(document).OfClass(typeof(Grid)).GetElementCount()},Json.Options));return;
            }
            if(job.Operation is "referenceFallbackCopy" or "columnBodyCopy" or "beamBodyCopy")
            {
                var source=WorkspacePath(job.SourceRvt);var output=WorkspacePath(job.OutputRvt);
                if(File.Exists(output))throw new IOException("Fallback output already exists");
                File.Copy(source,output,false);
                var document=ui.OpenAndActivateDocument(output).Document;
                bool columns=job.Operation=="columnBodyCopy";
                bool beams=job.Operation=="beamBodyCopy";
                var category=columns?BuiltInCategory.OST_StructuralColumns:beams?BuiltInCategory.OST_StructuralFraming:BuiltInCategory.OST_Walls;
                var fallbackReport=ReferenceFallback.Run(document,WorkspacePath(job.MeshManifestPath),WorkspacePath,(done,total)=>
                    File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status="running",phase="referenceFallback",done,total},Json.Options)),
                    category,columns?"ColumnBody":beams?"BeamBody":"WallGeometry");
                fallbackReport.Save(WorkspacePath(job.ReportPath));
                View3D? columnView=null;
                if(columns || beams)
                {
                    using var t=new Transaction(document,"Columns-only geometry check view");t.Start();
                    var vft=new FilteredElementCollector(document).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>().First(v=>v.ViewFamily==ViewFamily.ThreeDimensional);
                    columnView=View3D.CreateIsometric(document,vft.Id);columnView.Name=beams?"AC Beams Check":"AC Columns Check";columnView.DetailLevel=ViewDetailLevel.Fine;columnView.DisplayStyle=DisplayStyle.Shading;
                    var sourceView=new FilteredElementCollector(document).OfClass(typeof(View3D)).Cast<View3D>().FirstOrDefault(v=>v.Name=="AC Import Diagnostic");
                    if(sourceView!=null)columnView.SetOrientation(sourceView.GetOrientation());
                    foreach(Category cat in document.Settings.Categories)
                        if(columnView.CanCategoryBeHidden(cat.Id))columnView.SetCategoryHidden(cat.Id,cat.Id!=new ElementId(category));
                    t.Commit();
                }
                document.Save();if(columnView!=null)ui.ActiveUIDocument.RequestViewChange(columnView);
                File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status=fallbackReport.Items.Any(i=>i.Status is "blocked" or "revitError")?"completeWithErrors":"complete",output,seconds=timer.Elapsed.TotalSeconds,
                    outcomes=fallbackReport.Items.GroupBy(i=>i.Status).ToDictionary(g=>g.Key,g=>g.Count()),
                    beams=beams?new FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_StructuralFraming).WhereElementIsNotElementType().GetElementCount():0,
                    beamDirectShapes=beams?new FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_StructuralFraming).OfClass(typeof(DirectShape)).GetElementCount():0,
                    columns=columns?new FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_StructuralColumns).WhereElementIsNotElementType().GetElementCount():0,
                    nativeColumnInstances=columns?new FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_StructuralColumns).OfClass(typeof(FamilyInstance)).GetElementCount():0,
                    columnDirectShapes=columns?new FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_StructuralColumns).OfClass(typeof(DirectShape)).GetElementCount():0},Json.Options));
                return;
            }
            if(job.Operation=="geometryAudit")
            {
                var sourcePath=WorkspacePath(job.SourceRvt);
                var active=ui.ActiveUIDocument;
                if(active==null || !string.Equals(active.Document.PathName,sourcePath,StringComparison.OrdinalIgnoreCase))
                    active=ui.OpenAndActivateDocument(sourcePath);
                ISet<string>? keys=null;
                if(!string.IsNullOrEmpty(job.MeshKeysPath))keys=JsonSerializer.Deserialize<List<string>>(File.ReadAllText(WorkspacePath(job.MeshKeysPath)),Json.Options)!.ToHashSet(StringComparer.OrdinalIgnoreCase);
                if(job.GeometryCategory is not ("walls" or "columns" or "beams" or "doors" or "windows"))throw new InvalidDataException("Unsupported geometry audit category");
                var result=GeometryAudit.Write(active.Document,WorkspacePath(job.ReportPath),keys,(done,total)=>
                    File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status="running",phase="geometryAudit",done,total},Json.Options)),
                    job.GeometryCategory=="columns"?BuiltInCategory.OST_StructuralColumns:job.GeometryCategory=="beams"?BuiltInCategory.OST_StructuralFraming:
                    job.GeometryCategory=="doors"?BuiltInCategory.OST_Doors:job.GeometryCategory=="windows"?BuiltInCategory.OST_Windows:BuiltInCategory.OST_Walls);
                File.WriteAllText(resultPath,JsonSerializer.Serialize(new{status="complete",result,seconds=timer.Elapsed.TotalSeconds},Json.Options));
                return;
            }
            var manifestPath = WorkspacePath(job.ManifestPath);
            var outputPath = WorkspacePath(job.OutputRvt);
            if (File.Exists(outputPath)) throw new IOException("Diagnostic RVT already exists; choose a new path");
            if (job.Operation is not ("import" or "repairCopy")) throw new InvalidDataException("Unsupported diagnostic operation");
            Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);
            File.WriteAllText(resultPath, JsonSerializer.Serialize(new { status = "running", manifestPath, outputPath }, Json.Options));
            var manifest = Manifest.Load(manifestPath);
            var config = ImportConfig.Load(Path.Combine(Path.GetDirectoryName(manifestPath)!, "mapping.json"));
            if(job.Operation=="repairCopy")
            {
                var inputCopy=Path.ChangeExtension(outputPath,".input.rvt");
                File.Copy(WorkspacePath(job.SourceRvt),inputCopy,false);
                scratch=ui.Application.OpenDocumentFile(inputCopy);
            }
            else scratch = ui.Application.NewProjectDocument(WorkspacePath(job.TemplatePath));
            importer = new Importer(scratch, manifest, config);
            importer.Progress = (phase, done, total) => File.WriteAllText(resultPath,
                JsonSerializer.Serialize(new { status = "running", phase, done, total, seconds = timer.Elapsed.TotalSeconds }, Json.Options));
            var report = job.Operation=="repairCopy"?importer.RepairCopy():importer.Run();
            report.Save(WorkspacePath(job.ReportPath));
            using (var t = new Transaction(scratch, "Diagnostic 3D view"))
            {
                t.Start();
                var vft = new FilteredElementCollector(scratch).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>().First(v => v.ViewFamily == ViewFamily.ThreeDimensional);
                var view=new FilteredElementCollector(scratch).OfClass(typeof(View3D)).Cast<View3D>().FirstOrDefault(v=>v.Name=="AC Import Diagnostic") ?? View3D.CreateIsometric(scratch,vft.Id);
                view.Name = "AC Import Diagnostic"; view.DetailLevel = ViewDetailLevel.Fine;
                var planType=new FilteredElementCollector(scratch).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>().FirstOrDefault(v=>v.ViewFamily==ViewFamily.FloorPlan);
                if(planType!=null)
                    foreach(var level in new FilteredElementCollector(scratch).OfClass(typeof(Level)).Cast<Level>().ToList())
                    {
                        var tag=Storage.Read(level);
                        if(!tag.HasValue || tag.Value.Project!=manifest.Source.ProjectId || !tag.Value.Key.StartsWith("story:"))continue;
                        string name="AC Plan "+level.Name;
                        if(!new FilteredElementCollector(scratch).OfClass(typeof(ViewPlan)).Cast<ViewPlan>().Any(v=>v.Name==name))
                        {var plan=ViewPlan.Create(scratch,planType.Id,level.Id);plan.Name=name;plan.DetailLevel=ViewDetailLevel.Fine;}
                    }
                t.Commit();
            }
            scratch.SaveAs(outputPath, new SaveAsOptions { OverwriteExistingFile = false });
            var counts = new { walls = new FilteredElementCollector(scratch).OfClass(typeof(Wall)).GetElementCount(),
                floors = new FilteredElementCollector(scratch).OfClass(typeof(Floor)).GetElementCount(),
                rooms = new FilteredElementCollector(scratch).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType().GetElementCount(),
                directShapes = new FilteredElementCollector(scratch).OfClass(typeof(DirectShape)).GetElementCount() };
            scratch.Close(false); scratch = null;
            var opened = ui.OpenAndActivateDocument(outputPath);
            string viewWarning = "";
            try
            {
                // Request a view change after API processing has finished.
                opened.RequestViewChange(new FilteredElementCollector(opened.Document).OfClass(typeof(View3D)).Cast<View3D>().First(v => v.Name == "AC Import Diagnostic"));
            }
            catch (Exception viewError) { viewWarning = viewError.Message; }
            File.WriteAllText(resultPath, JsonSerializer.Serialize(new { status = "complete", outputPath, counts, viewWarning, seconds = timer.Elapsed.TotalSeconds,
                outcomes = report.Items.GroupBy(i => i.Status).ToDictionary(g => g.Key, g => g.Count()) }, Json.Options));
        }
        catch (Exception ex)
        {
            try { importer?.Report.Save(Path.ChangeExtension(resultPath, ".report.json")); } catch { }
            if (scratch != null)
            {
                try { scratch.SaveAs(WorkspacePath(Path.ChangeExtension(resultPath, ".recovery.rvt")), new SaveAsOptions { OverwriteExistingFile = false }); scratch.Close(false); } catch { }
            }
            File.WriteAllText(resultPath, JsonSerializer.Serialize(new { status = "failed", error = ex.ToString(), seconds = timer.Elapsed.TotalSeconds }, Json.Options));
        }
        finally { busy = false; }
    }
    sealed class Job
    {
        public string Operation { get; set; } = "";
        public string ManifestPath { get; set; } = "";
        public string TemplatePath { get; set; } = "";
        public string OutputRvt { get; set; } = "";
        public string ReportPath { get; set; } = "";
        public string ResultPath { get; set; } = "";
        public string SourceRvt { get; set; } = "";
        public string MeshKeysPath { get; set; } = "";
        public string MeshManifestPath { get; set; } = "";
        public string ReferenceIFCPath { get; set; } = "";
        public string ReferenceIFCSha256 { get; set; } = "";
        public string GeometryCategory { get; set; } = "walls";
        public string MappingPath {get;set;}="";
        public int Limit {get;set;}
    }
}
