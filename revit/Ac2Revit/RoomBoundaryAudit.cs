using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Architecture;
using System.Text.Json;

namespace Ac2Revit;

/// <summary>Largest failed rooms first. Every candidate room is rolled back.</summary>
public static class RoomBoundaryAudit
{
    public static void Write(Document doc,Manifest manifest,Report spatial,string output,int limit=20,Action<string,int,int>? progress=null)
    {
        var index=Storage.Index(doc,manifest.Source.ProjectId);var records=manifest.Elements.ToDictionary(e=>e.SourceGuid,StringComparer.OrdinalIgnoreCase);
        var selected=spatial.ZoneAreas.Where(a=>!a.Accepted).OrderByDescending(a=>a.SourceAreaSquareMeters).Take(limit).ToList();
        var geometry=new Geometry(doc,manifest.Coordinates.Transform);var results=new List<object>();
        var physical=new FilteredElementCollector(doc).WhereElementIsNotElementType().Where(e=>e is Wall || e.Category?.Id==new ElementId(BuiltInCategory.OST_StructuralColumns)).ToList();
        double[] At(XYZ p)=>[UnitUtils.ConvertFromInternalUnits(p.X,UnitTypeId.Meters),UnitUtils.ConvertFromInternalUnits(p.Y,UnitTypeId.Meters),UnitUtils.ConvertFromInternalUnits(p.Z,UnitTypeId.Meters)];
        object ElementInfo(Element? e)
        {
            if(e==null)return new{missing=true};
            var flags=e.Parameters.Cast<Parameter>().Where(p=>p.Id.Value<0 && ((BuiltInParameter)p.Id.Value).ToString().Contains("ROOM_BOUNDING"))
                .ToDictionary(p=>((BuiltInParameter)p.Id.Value).ToString(),p=>p.StorageType==StorageType.Integer?p.AsInteger().ToString():p.AsValueString()??"");
            var box=e.get_BoundingBox(null);
            return new{id=e.Id.Value,sourceGuid=Storage.Read(e)?.Key??"",elementClass=e.GetType().Name,category=e.Category?.Name,name=e.Name,
                roomBounding=flags,min=box==null?null:At(box.Min),max=box==null?null:At(box.Max)};
        }
        foreach(var area in selected)
        {
            var record=records[area.SourceGuid];var payload=record.Payload;var level=(Level)index[record.LevelKey];
            var loops=payload.GetProperty("loops").Read<List<List<Edge>>>();
            var source=loops.Select(l=>SourcePlanarGeometry.DisplayPoints(l).Select(p=>At(geometry.Xyz(p))).ToArray()).ToArray();
            var all=source.SelectMany(l=>l).ToList();double x0=all.Min(p=>p[0]),x1=all.Max(p=>p[0]),y0=all.Min(p=>p[1]),y1=all.Max(p=>p[1]);
            double computation=level.get_Parameter(BuiltInParameter.LEVEL_ROOM_COMPUTATION_HEIGHT)?.AsDouble()??0;
            double z=level.Elevation+computation+Geometry.Feet(0.001);
            var near=physical.Where(e=>
            {
                var b=e.get_BoundingBox(null);return b!=null && b.Min.Z<=z && b.Max.Z>=z && b.Max.X>=Geometry.Feet(x0-0.5) && b.Min.X<=Geometry.Feet(x1+0.5) && b.Max.Y>=Geometry.Feet(y0-0.5) && b.Min.Y<=Geometry.Feet(y1+0.5);
            }).ToList();
            var scene=near.Select(e=>new{element=ElementInfo(e),sectionSegments=e.Category?.Id==new ElementId(BuiltInCategory.OST_StructuralColumns)?Section(e,z).Select(s=>s.Select(At).ToArray()).ToArray():null}).ToArray();
            var modes=new Dictionary<string,object>();double? actualArea=null;string error="";
            using(var transaction=new Transaction(doc,"Read-only room boundary candidate"))
            {
                try
                {
                    transaction.Start();var p=geometry.Xyz(payload.GetProperty("point").Read<Point>());
                    var room=doc.Create.NewRoom(level,new UV(p.X,p.Y));
                    room.get_Parameter(BuiltInParameter.ROOM_LOWER_OFFSET)?.Set(Geometry.Feet(payload.D("baseOffset")));doc.Regenerate();
                    actualArea=UnitUtils.ConvertFromInternalUnits(room.Area,UnitTypeId.SquareMeters);
                    foreach(var mode in new[]{SpatialElementBoundaryLocation.Finish,SpatialElementBoundaryLocation.Center,SpatialElementBoundaryLocation.CoreBoundary,SpatialElementBoundaryLocation.CoreCenter})
                    {
                        using var options=new SpatialElementBoundaryOptions{SpatialElementBoundaryLocation=mode};
                        try
                        {
                            var boundary=room.GetBoundarySegments(options);
                            modes[mode.ToString()]=boundary==null?Array.Empty<object>():boundary.Select(ring=>ring.Select(segment=>new{element=ElementInfo(doc.GetElement(segment.ElementId)),curve=segment.GetCurve().GetType().Name,points=segment.GetCurve().Tessellate().Select(At).ToArray()}).ToArray()).ToArray();
                        }
                        catch(Exception ex){modes[mode.ToString()]=new{error=ex.Message};}
                    }
                }
                catch(Exception ex){error=ex.ToString();}
                finally{if(transaction.GetStatus()==TransactionStatus.Started)transaction.RollBack();}
            }
            results.Add(new{rank=results.Count+1,sourceGuid=record.SourceGuid,name=payload.S("name"),number=payload.S("number"),levelKey=record.LevelKey,levelName=level.Name,
                sourceManual=record.Raw.GetProperty("details").B("isManual"),sourceArea=area.SourceAreaSquareMeters,reportedArea=area.RevitAreaSquareMeters,probeArea=actualArea,
                sourceLoops=source,sourceCurveEdgesExact=loops,sourceDisplaySagittaMeters=0.001,sourcePoint=payload.GetProperty("point"),sourceBaseOffset=payload.D("baseOffset"),levelElevation=UnitUtils.ConvertFromInternalUnits(level.Elevation,UnitTypeId.Meters),
                computationHeight=UnitUtils.ConvertFromInternalUnits(computation,UnitTypeId.Meters),sectionPlaneZ=UnitUtils.ConvertFromInternalUnits(z,UnitTypeId.Meters),
                boundaryModes=modes,nearbyPhysicalElements=scene,error});
            progress?.Invoke("largestRoomBoundaries",results.Count,selected.Count);
        }
        File.WriteAllText(output,JsonSerializer.Serialize(new{document=doc.PathName,sourceProject=manifest.Source.ProjectId,
            areaBoundaryMode=AreaVolumeSettings.GetAreaVolumeSettings(doc).GetSpatialElementBoundaryLocation(SpatialElementType.Room).ToString(),
            ordering="Failed rooms descending by source polygon area",probeTransactions="All rolled back",sectionNote="Column triangle sections at level computation plane +1 mm; diagnostic only",rooms=results},Json.Options));
    }
    static List<XYZ[]> Section(Element element,double z)
    {
        var segments=new List<XYZ[]>();
        void Mesh(Mesh mesh)
        {
            for(int i=0;i<mesh.NumTriangles;i++)
            {
                var tri=mesh.get_Triangle(i);var p=Enumerable.Range(0,3).Select(tri.get_Vertex).ToArray();var hits=new List<XYZ>();
                for(int j=0;j<3;j++)
                {
                    var a=p[j];var b=p[(j+1)%3];double da=a.Z-z,db=b.Z-z;
                    if(Math.Abs(da)<1e-8 && Math.Abs(db)<1e-8)continue;
                    if(Math.Abs(da)<1e-8)hits.Add(a);
                    else if(da*db<0)hits.Add(a+(b-a)*(-da/(db-da)));
                }
                var unique=new List<XYZ>();foreach(var pnt in hits)if(unique.All(q=>q.DistanceTo(pnt)>1e-7))unique.Add(pnt);
                if(unique.Count==2 && unique[0].DistanceTo(unique[1])>1e-7)segments.Add(unique.ToArray());
            }
        }
        void Read(GeometryElement g)
        {
            foreach(var obj in g)
                if(obj is GeometryInstance instance)Read(instance.GetInstanceGeometry());
                else if(obj is Mesh mesh)Mesh(mesh);
                else if(obj is Solid solid)foreach(Face face in solid.Faces)Mesh(face.Triangulate());
        }
        var g=element.get_Geometry(new Options{DetailLevel=ViewDetailLevel.Fine});if(g!=null)Read(g);return segments;
    }
}
