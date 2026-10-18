using Autodesk.Revit.DB;

namespace Ac2Revit;

public sealed class Geometry
{
    readonly RigidTransform transform;
    readonly double shortCurve;
    public Geometry(Document doc, RigidTransform transform) { this.transform = transform; shortCurve = doc.Application.ShortCurveTolerance; }
    public static double Feet(double meters) => UnitUtils.ConvertToInternalUnits(meters, UnitTypeId.Meters);
    public XYZ Xyz(Point p)
    {
        if (!p.Finite) throw new InvalidDataException("Invalid coordinate");
        double c = Math.Cos(transform.RotationRadians), s = Math.Sin(transform.RotationRadians);
        var xyz = new XYZ(Feet(p.X*c-p.Y*s+transform.Translation.X), Feet(p.X*s+p.Y*c+transform.Translation.Y), Feet(p.Z+transform.Translation.Z));
        if (!XYZ.IsWithinLengthLimits(xyz)) throw new InvalidDataException("Coordinate outside Revit design limits");
        return xyz;
    }
    public Curve Curve(Edge edge, double? flatZ = null)
    {
        XYZ At(Point p) { var q = Xyz(p); return flatZ.HasValue ? new XYZ(q.X, q.Y, flatZ.Value) : q; }
        var a = At(edge.Start); var b = At(edge.End);
        if (a.DistanceTo(b) < shortCurve) throw new InvalidDataException("Edge below Revit ShortCurveTolerance; geometry was not altered");
        Curve result = edge.Kind switch { "line" => Line.CreateBound(a, b), "arc" when edge.Mid != null => Arc.Create(a, b, At(edge.Mid)), _ => throw new InvalidDataException("Unknown curve kind") };
        if (result.Length < shortCurve) throw new InvalidDataException("Short curve");
        return result;
    }
    public List<CurveLoop> Loops(List<List<Edge>> loops, double elevation)
    {
        var result = new List<CurveLoop>();
        for (int i = 0; i < loops.Count; ++i)
        {
            var loop = CurveLoop.Create(loops[i].Select(e => Curve(e, elevation)).ToList());
            if (loop.IsOpen() || !loop.HasPlane()) throw new InvalidDataException("Open/nonplanar loop");
            bool ccw = loop.IsCounterclockwise(XYZ.BasisZ);
            if ((i == 0) != ccw) loop.Flip();
            result.Add(loop);
        }
        return result;
    }
}
