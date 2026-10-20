using System.Text.Json;

namespace Ac2Revit;

/// <summary>Recognise only an explicit zero-width, straight basic source wall.</summary>
public static class SourceZeroThicknessWall
{
    public static bool IsCandidate(ElementRecord record)
    {
        if(record.Kind!="Wall" || record.Raw.ValueKind!=JsonValueKind.Object || !record.Raw.TryGetProperty("details",out var d))return false;
        bool Zero(string key)=>d.TryGetProperty(key,out var p) && p.ValueKind==JsonValueKind.Number && p.GetDouble()==0;
        return d.S("geometryType")=="Straight" && d.S("structureType")=="Basic" && d.S("profileType")=="Normal"
            && Zero("begThickness") && Zero("endThickness") && Zero("arcAngle");
    }
    public static (Edge Curve,double Height,bool RoomBoundary) Read(ElementRecord record)
    {
        if(!IsCandidate(record))throw new InvalidDataException("Not an explicit supported zero-thickness wall");
        var d=record.Raw.GetProperty("details");
        double z=d.GetProperty("zCoordinate").GetDouble(),height=d.GetProperty("height").GetDouble();
        if(!double.IsFinite(z) || !double.IsFinite(height) || height<=0 || d.GetProperty("offset").GetDouble()!=0)
            throw new InvalidDataException("Zero-thickness wall elevation/height/offset is unsupported");
        var a=d.GetProperty("begCoordinate").Read<Point>();var b=d.GetProperty("endCoordinate").Read<Point>();a.Z=b.Z=z;
        if(!a.Finite || !b.Finite || (a.X==b.X && a.Y==b.Y))throw new InvalidDataException("Invalid zero-thickness wall axis");
        if(d.S("zoneRel") is not ("Boundary" or "None" or "ReduceArea" or "SubtractFromZone"))
            throw new InvalidDataException("Unknown zero-thickness wall zone relation");
        return (new Edge{Kind="line",Start=a,End=b},height,d.S("zoneRel")=="Boundary");
    }
}
