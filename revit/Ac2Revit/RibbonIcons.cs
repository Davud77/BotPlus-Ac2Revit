using System.Windows;
using System.Windows.Media;

namespace Ac2Revit;

/// <summary>Embedded vector artwork, sharp at 16/32 px and independent of external icon files.</summary>
internal static class RibbonIcons
{
    public static ImageSource Make(string kind,int size)
    {
        var drawing=new DrawingGroup();
        using(var c=drawing.Open())
        {
            var blue=new SolidColorBrush(Color.FromRgb(32,133,218));
            var green=new SolidColorBrush(Color.FromRgb(42,163,109));
            var pale=new SolidColorBrush(Color.FromRgb(229,240,251));
            var dark=new Pen(new SolidColorBrush(Color.FromRgb(59,84,112)),1.6){StartLineCap=PenLineCap.Round,EndLineCap=PenLineCap.Round,LineJoin=PenLineJoin.Round};
            void Line(double x,double y,double a,double b,Pen? pen=null)=>c.DrawLine(pen??dark,new System.Windows.Point(x,y),new System.Windows.Point(a,b));
            void Arrow(double x,double y,Brush brush)
            {
                var p=new Pen(brush,2.6){StartLineCap=PenLineCap.Round,EndLineCap=PenLineCap.Round};
                Line(x,y,x+12,y,p);Line(x+8,y-4,x+12,y,p);Line(x+8,y+4,x+12,y,p);
            }
            if(kind=="folder")
            {
                c.DrawGeometry(new SolidColorBrush(Color.FromRgb(242,185,62)),dark,System.Windows.Media.Geometry.Parse("M3,9 L13,9 L16,12 L29,12 L27,27 L3,27 Z"));
                Line(5,16,26,16,new Pen(Brushes.White,1.5));
            }
            else if(kind=="transfer")
            {
                c.DrawRectangle(pale,dark,new Rect(2,5,10,21));c.DrawRectangle(pale,dark,new Rect(21,5,9,21));
                for(int y=9;y<=21;y+=6){Line(5,y,9,y);Line(24,y,27,y);}
                Arrow(9,16,green);
            }
            else if(kind=="export")
            {
                c.DrawGeometry(pale,dark,System.Windows.Media.Geometry.Parse("M3,27 L3,9 L11,4 L19,9 L19,27 Z"));
                Line(7,12,14,12);Line(7,17,14,17);Line(7,22,12,22);Arrow(16,18,green);
            }
            else if(kind=="build")
            {
                c.DrawGeometry(pale,dark,System.Windows.Media.Geometry.Parse("M4,13 L16,7 L28,13 L28,25 L16,31 L4,25 Z M4,13 L16,19 L28,13 M16,19 L16,31"));
                Arrow(2,4,blue);
            }
            else if(kind=="resume")
            {
                c.DrawRoundedRectangle(pale,dark,new Rect(4,3,17,25),2,2);
                Line(8,8,16,8);Line(8,12,16,12);
                c.DrawEllipse(Brushes.White,new Pen(blue,2),new System.Windows.Point(22,23),8,8);
                Line(22,18,22,23,new Pen(blue,2));Line(22,23,26,24,new Pen(green,2));
            }
            else if(kind=="preview")
            {
                c.DrawRoundedRectangle(pale,dark,new Rect(4,3,18,25),2,2);
                for(int y=8;y<=18;y+=5)Line(8,y,17,y);
                c.DrawEllipse(Brushes.White,new Pen(blue,2.5),new System.Windows.Point(21,21),6,6);Line(25,25,30,30,new Pen(blue,3));
            }
            else
            {
                c.DrawRoundedRectangle(pale,dark,new Rect(9,3,18,25),2,2);
                Arrow(2,16,blue);Line(17,9,23,9);Line(17,22,23,22);
            }
        }
        drawing.Transform=new ScaleTransform(size/32.0,size/32.0);drawing.Freeze();
        var result=new DrawingImage(drawing);result.Freeze();return result;
    }
}
