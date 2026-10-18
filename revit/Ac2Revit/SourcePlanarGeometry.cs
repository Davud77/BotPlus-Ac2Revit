namespace Ac2Revit;

/// <summary>Source coordinates in metres. Exact circular-segment areas; sampling is display-only.</summary>
public static class SourcePlanarGeometry
{
    public readonly record struct Circle(double X,double Y,double Radius,double Start,double Sweep);
    static double Positive(double angle)=>(angle%Math.Tau+Math.Tau)%Math.Tau;
    public static Circle ArcCircle(Edge edge)
    {
        if(edge.Kind!="arc" || edge.Mid==null)throw new InvalidDataException("Missing circular arc midpoint");
        double dx=edge.End.X-edge.Start.X,dy=edge.End.Y-edge.Start.Y,mx=edge.Mid.X-edge.Start.X,my=edge.Mid.Y-edge.Start.Y;
        double determinant=2*(dx*my-dy*mx);
        if(Math.Abs(determinant)<1e-18)throw new InvalidDataException("Collinear source arc points");
        double b2=dx*dx+dy*dy,m2=mx*mx+my*my;
        double ox=(b2*my-m2*dy)/determinant,oy=(dx*m2-mx*b2)/determinant;
        double radius=Math.Sqrt(ox*ox+oy*oy),start=Math.Atan2(-oy,-ox);
        double ccw=Positive(Math.Atan2(dy-oy,dx-ox)-start),mid=Positive(Math.Atan2(my-oy,mx-ox)-start);
        double sweep=mid<ccw?ccw:ccw-Math.Tau;
        if(!double.IsFinite(radius)||!double.IsFinite(sweep)||radius<=0||Math.Abs(sweep)<1e-12)throw new InvalidDataException("Invalid source arc circle");
        return new(edge.Start.X+ox,edge.Start.Y+oy,radius,start,sweep);
    }
    static double SegmentTerm(double angle)
    {
        if(Math.Abs(angle)>=1e-3)return angle-Math.Sin(angle);
        double square=angle*angle;return angle*square*(1.0/6-square/120+square*square/5040);
    }
    public static double SignedArea(IReadOnlyList<Edge> edges)
    {
        if(edges.Count==0)throw new InvalidDataException("Empty source area loop");
        double x=edges[0].Start.X,y=edges[0].Start.Y,area=0;
        foreach(var edge in edges)
        {
            area+=((edge.Start.X-x)*(edge.End.Y-y)-(edge.End.X-x)*(edge.Start.Y-y))/2;
            if(edge.Kind=="arc")
            {
                var arc=ArcCircle(edge);area+=arc.Radius*arc.Radius*SegmentTerm(arc.Sweep)/2;
            }
            else if(edge.Kind!="line")throw new InvalidDataException("Unsupported source area curve "+edge.Kind);
        }
        if(!double.IsFinite(area))throw new InvalidDataException("Nonfinite source area");return area;
    }
    public static List<Point> DisplayPoints(IEnumerable<Edge> edges,double sagittaMeters=0.001)
    {
        var result=new List<Point>();
        foreach(var edge in edges)
        {
            result.Add(edge.Start);
            if(edge.Kind!="arc")continue;
            var a=ArcCircle(edge);double step=Math.Max(1e-8,2*Math.Acos(Math.Clamp(1-sagittaMeters/a.Radius,-1,1)));
            int count=(int)Math.Ceiling(Math.Abs(a.Sweep)/step);
            if(count>100000)throw new InvalidDataException("Diagnostic arc sampling limit");
            for(int i=1;i<count;i++){double angle=a.Start+a.Sweep*i/count;result.Add(new Point{X=a.X+a.Radius*Math.Cos(angle),Y=a.Y+a.Radius*Math.Sin(angle),Z=edge.Start.Z});}
        }
        return result;
    }
}
