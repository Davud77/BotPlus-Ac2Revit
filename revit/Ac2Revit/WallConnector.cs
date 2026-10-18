using Autodesk.Revit.DB;

namespace Ac2Revit;

/// <summary>Explicit source topology first; conservative exact reference-line fallback.</summary>
public sealed class WallConnector(Document doc, ImportConfig config, Report report,RigidTransform transform,Action<string,int,int>? progress=null)
{
    sealed record Item(string Guid, Wall Wall, Curve Curve,Curve Reference, BoundingBoxXYZ Box);
    public void Run(IReadOnlyDictionary<string, Element> imported, IEnumerable<ElementRecord> records)
    {
        double tol = Geometry.Feet(config.WallJoinToleranceMeters);
        var recordList = records.ToList();var recordIndex=recordList.ToDictionary(r=>r.SourceGuid,StringComparer.OrdinalIgnoreCase);
        var sourceGeometry=new Geometry(doc,transform);
        var walls = imported.Where(p => p.Value is Wall).Select(p => {
            var w = (Wall)p.Value;
            return new Item(p.Key, w, ((LocationCurve)w.Location).Curve,sourceGeometry.Curve(recordIndex[p.Key].Payload.GetProperty("curve").Read<Edge>()), w.get_BoundingBox(null));
        }).Where(w => w.Box != null).ToDictionary(w => w.Guid, StringComparer.OrdinalIgnoreCase);
        var endings = new HashSet<(string Guid, int End)>();
        var cuts = new HashSet<(string A, string B)>();
        var sourcePairs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var sourceKnown = recordList.Where(r => r.Raw.ValueKind == System.Text.Json.JsonValueKind.Object &&
            r.Raw.TryGetProperty("relations",out var rel) && rel.TryGetProperty("wallConnections",out _)).Select(r=>r.SourceGuid).ToHashSet(StringComparer.OrdinalIgnoreCase);
        var sourceNeighbors = recordList.ToDictionary(r=>r.SourceGuid,r=>r.WallConnections.Select(c=>c.TargetGuid).ToHashSet(StringComparer.OrdinalIgnoreCase),StringComparer.OrdinalIgnoreCase);
        int unavailable = 0, ambiguous = 0;
        string Pair(string a, string b) => string.Compare(a, b, StringComparison.OrdinalIgnoreCase) < 0 ? a+"|"+b : b+"|"+a;
        bool SameHeight(Item a, Item b) => Math.Min(a.Box.Max.Z,b.Box.Max.Z)-Math.Max(a.Box.Min.Z,b.Box.Min.Z) > tol;
        bool InBox(XYZ p, BoundingBoxXYZ b) => p.X >= b.Min.X-tol && p.X <= b.Max.X+tol && p.Y >= b.Min.Y-tol && p.Y <= b.Max.Y+tol;
        bool OnReference(XYZ p, Item other,bool original=false)
        {
            var target=original?other.Reference:other.Curve;
            var q = new XYZ(p.X,p.Y,target.GetEndPoint(0).Z);
            var projected = target.Project(q);
            if (projected == null || projected.Distance > tol) return false;
            double u = target.ComputeNormalizedParameter(projected.Parameter);
            return u >= -1e-8 && u <= 1+1e-8;
        }
        bool OverlappingAxes(Item a, Item b)
        {
            if (a.Curve is not Line la || b.Curve is not Line lb) return false;
            var da = (la.GetEndPoint(1)-la.GetEndPoint(0)).Normalize();
            var db = (lb.GetEndPoint(1)-lb.GetEndPoint(0)).Normalize();
            if (Math.Abs(da.DotProduct(db)) < 1-1e-8) return false;
            var p = la.GetEndPoint(0); var q = lb.GetEndPoint(0);
            if (Math.Abs((q.X-p.X)*da.Y-(q.Y-p.Y)*da.X) > tol) return false;
            double x0=(q-p).DotProduct(da), x1=(lb.GetEndPoint(1)-p).DotProduct(da);
            return Math.Min(la.Length,Math.Max(x0,x1))-Math.Max(0,Math.Min(x0,x1)) > tol;
        }
        void EndIfTouch(Item a, Item b, int end, bool source,bool sourceAxis=false)
        {
            var p = a.Curve.GetEndPoint(end);
            if (source ? InBox(p,b.Box)||InBox(a.Reference.GetEndPoint(end),b.Box) : sourceAxis?OnReference(a.Reference.GetEndPoint(end),b,true):OnReference(p,b)) endings.Add((a.Guid,end));
        }
        foreach (var record in recordList.Where(r => r.Kind == "Wall"))
        {
            if (!walls.TryGetValue(record.SourceGuid,out var a)) continue;
            foreach (var connection in record.WallConnections)
            {
                if (!walls.TryGetValue(connection.TargetGuid,out var b)) { unavailable++; continue; }
                if (a.Guid.Equals(b.Guid,StringComparison.OrdinalIgnoreCase) || !SameHeight(a,b)) continue;
                sourcePairs.Add(Pair(a.Guid,b.Guid));
                if (OverlappingAxes(a,b)) { ambiguous++; continue; }
                if (connection.OwnerEnd is 0 or 1)
                {
                    // conBeg/conEnd: owner's specified end meets the neighbor's endpoint.
                    EndIfTouch(a,b,connection.OwnerEnd.Value,true);
                    EndIfTouch(b,a,connection.TargetWithBeginPoint?0:1,true);
                }
                else if(connection.Relation=="connectedWithReferenceLineToEndPoints")
                {
                    // conRef: OWNER endpoint meets the neighbor's reference-line interior.
                    EndIfTouch(a,b,0,false,true);EndIfTouch(a,b,1,false,true);
                }
                else if(connection.Relation=="connectedToReferenceLine")
                {
                    // con: NEIGHBOR endpoint meets the owner's reference-line interior.
                    EndIfTouch(b,a,connection.TargetWithBeginPoint?0:1,false,true);
                }
                // Boolean cleanup only for authoritative source relations, never mere proximity.
                if (connection.Relation == "crossingReferenceLine")
                {
                    var sorted = string.Compare(a.Guid,b.Guid,StringComparison.OrdinalIgnoreCase) < 0 ? (a.Guid,b.Guid) : (b.Guid,a.Guid);
                    cuts.Add(sorted);
                }
            }
        }
        // Spatial grid avoids comparing every wall against every other wall.
        double cell = Geometry.Feet(5);
        var grid = new Dictionary<(int X,int Y),List<Item>>();
        foreach (var w in walls.Values)
            for(int x=(int)Math.Floor((w.Box.Min.X-tol)/cell);x<=(int)Math.Floor((w.Box.Max.X+tol)/cell);x++)
                for(int y=(int)Math.Floor((w.Box.Min.Y-tol)/cell);y<=(int)Math.Floor((w.Box.Max.Y+tol)/cell);y++)
                {
                    if(!grid.TryGetValue((x,y),out var bucket)) grid[(x,y)] = bucket = [];
                    bucket.Add(w);
                }
        foreach (var a in walls.Values)
            for(int end=0;end<2;end++)
            {
                var p=a.Curve.GetEndPoint(end);
                if(!grid.TryGetValue(((int)Math.Floor(p.X/cell),(int)Math.Floor(p.Y/cell)),out var candidates)) continue;
                foreach(var b in candidates)
                {
                    if(a.Guid.Equals(b.Guid,StringComparison.OrdinalIgnoreCase) || sourceKnown.Contains(a.Guid) || sourceKnown.Contains(b.Guid) || sourcePairs.Contains(Pair(a.Guid,b.Guid)) || !SameHeight(a,b) || OverlappingAxes(a,b)) continue;
                    // Fallback permits only an endpoint on another bound reference curve within 1 mm.
                    EndIfTouch(a,b,end,false);
                }
            }
        int enabled=0,joined=0,verified=0,rejected=0,failedEnds=0,processed=0;
        var nativeIds=walls.Values.ToDictionary(w=>w.Wall.Id,w=>w.Guid);
        var plan=endings.OrderBy(e=>e.Guid,StringComparer.Ordinal).ThenBy(e=>e.End).ToArray();
        void ApplyBatch((string Guid,int End)[] batch)
        {
            var local=new Report();var checks=new List<WallEndJoinCheck>();bool committed=false;
            using(var transaction=new Transaction(doc,"Archicad wall ends"))
            {
                try
                {
                    transaction.Start();
                    var options=transaction.GetFailureHandlingOptions();
                    options.SetFailuresPreprocessor(new JoinFailures(local,nativeIds));options.SetClearAfterRollback(true);transaction.SetFailureHandlingOptions(options);
                    foreach(var end in batch)WallUtils.AllowWallJoinAtEnd(walls[end.Guid].Wall,end.End);
                    doc.Regenerate();
                    foreach(var end in batch)
                    {
                        var owner=walls[end.Guid].Wall;
                        var neighbors=((LocationCurve)owner.Location).get_ElementsAtJoin(end.End).Cast<Element>().Where(n=>n.Id!=owner.Id && n is Wall).ToList();
                        bool invalid=sourceKnown.Contains(end.Guid) && neighbors.Any(n=>!nativeIds.TryGetValue(n.Id,out var g) || !sourceNeighbors[end.Guid].Contains(g));
                        if(invalid)WallUtils.DisallowWallJoinAtEnd(owner,end.End);
                        checks.Add(new(end.Guid,end.End,sourceKnown.Contains(end.Guid),neighbors.Select(n=>nativeIds.GetValueOrDefault(n.Id,n.UniqueId)).ToList(),
                            invalid?"rejected":neighbors.Count>0?"verified":"allowedNoNativeNeighbor"));
                    }
                    doc.Regenerate();committed=transaction.Commit()==TransactionStatus.Committed;
                }
                catch(Exception ex)
                {
                    if(transaction.GetStatus()==TransactionStatus.Started)transaction.RollBack();
                    local.Add("wall-topology","WallJoin","revitError",ex.Message);
                }
            }
            if(committed)
            {
                report.Items.AddRange(local.Items);report.WallJoins.AddRange(checks);
                rejected+=checks.Count(c=>c.Status=="rejected");verified+=checks.Count(c=>c.Status=="verified");enabled+=checks.Count(c=>c.Status!="rejected");
                foreach(var c in checks.Where(c=>c.Status=="rejected"))report.Add(c.SourceGuid,"WallJoin","blocked",$"End {c.End}: native neighbors are outside source topology.");
                processed+=batch.Length;progress?.Invoke("wallEndJoins",processed,plan.Length);return;
            }
            // A failure never discards an already committed compatible batch.
            if(batch.Length>1)
            {
                int half=batch.Length/2;ApplyBatch(batch[..half]);ApplyBatch(batch[half..]);return;
            }
            var failed=batch[0];failedEnds++;processed++;
            report.WallJoins.Add(new(failed.Guid,failed.End,sourceKnown.Contains(failed.Guid),[],"blockedByRevit"));
            report.Add(failed.Guid,"WallJoin","blocked",$"End {failed.End}: "+string.Join(" | ",local.Items.Where(i=>i.Status=="revitError").Select(i=>i.Message).Distinct()));
            if(!config.AllowPartial)throw new InvalidOperationException("Revit rejected a source wall end join");
            progress?.Invoke("wallEndJoins",processed,plan.Length);
        }
        for(int start=0;start<plan.Length;start+=64)ApplyBatch(plan[start..Math.Min(start+64,plan.Length)]);
        var cutPlan=cuts.OrderBy(p=>p.A,StringComparer.Ordinal).ThenBy(p=>p.B,StringComparer.Ordinal).ToArray();int cutDone=0;
        foreach(var pair in cutPlan)
        {
            var a=walls[pair.A].Wall;var b=walls[pair.B].Wall;
            using var transaction=new Transaction(doc,"Archicad wall crossing");var local=new Report();
            try
            {
                if(!JoinGeometryUtils.AreElementsJoined(doc,a,b))
                {
                    transaction.Start();var options=transaction.GetFailureHandlingOptions();
                    options.SetFailuresPreprocessor(new JoinFailures(local,nativeIds));options.SetClearAfterRollback(true);transaction.SetFailureHandlingOptions(options);
                    JoinGeometryUtils.JoinGeometry(doc,a,b);doc.Regenerate();
                    if(transaction.Commit()==TransactionStatus.Committed){joined++;report.Items.AddRange(local.Items);}
                    else report.Add(pair.A+"|"+pair.B,"WallJoin","skipped",string.Join(" | ",local.Items.Where(i=>i.Status=="revitError").Select(i=>i.Message).Distinct()));
                }
            }
            catch(Exception ex)
            {
                if(transaction.GetStatus()==TransactionStatus.Started)transaction.RollBack();
                report.Add(pair.A+"|"+pair.B,"WallJoin","skipped",ex.Message);
                if(!config.AllowPartial)throw;
            }
            progress?.Invoke("wallCrossings",++cutDone,cutPlan.Length);
        }
        report.Add("wall-topology","WallJoin","joined",$"Committed allowed ends: {enabled}; verified native joined ends: {verified}; rejected native proposals: {rejected}; ends blocked by Revit: {failedEnds}; source boolean joins: {joined}; unavailable hosts: {unavailable}; ambiguous axis overlaps skipped: {ambiguous}; walls with source topology: {sourceKnown.Count}. Fallback tolerance: {config.WallJoinToleranceMeters} m.");
    }
    sealed class JoinFailures(Report report,IReadOnlyDictionary<ElementId,string> ids) : IFailuresPreprocessor
    {
        public FailureProcessingResult PreprocessFailures(FailuresAccessor accessor)
        {
            bool error=false;
            foreach(var f in accessor.GetFailureMessages())
            {
                string affected=string.Join(",",f.GetFailingElementIds().Select(id=>ids.GetValueOrDefault(id,id.Value.ToString())));
                report.Add("wall-topology","WallJoin",f.GetSeverity()==FailureSeverity.Warning?"warning":"revitError",f.GetDescriptionText()+" ["+affected+"]");
                if(f.GetSeverity()==FailureSeverity.Warning)accessor.DeleteWarning(f);else error=true;
            }
            return error?FailureProcessingResult.ProceedWithRollBack:FailureProcessingResult.Continue;
        }
    }
}
