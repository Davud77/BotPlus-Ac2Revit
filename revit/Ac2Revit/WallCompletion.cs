using Autodesk.Revit.DB;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Ac2Revit;

/// <summary>Every source wall is accounted for; geometric substitutes are visibly labelled.</summary>
public static class WallCompletion
{
    public const string Incomplete="НЕПОЛНОЦЕННАЯ СТЕНА";
    static void Annotate(Parameter? parameter,string note)
    {
        if(parameter==null || parameter.IsReadOnly)return;
        string old=parameter.AsString()??"";
        if(!old.Contains(note,StringComparison.Ordinal))parameter.Set(old.Length==0?note:old+"\n"+note);
    }
    public static Report Run(Document doc,Manifest manifest,string? bodyPath,string output,Action<string,int,int>? progress=null)
    {
        Directory.CreateDirectory(output);string project=manifest.Source.ProjectId;
        var report=new Report{ProjectId=project,Mode="wallCompletion"};
        var walls=manifest.Elements.Where(e=>e.Kind=="Wall").ToList();var index=Storage.Index(doc,project);
        if(!string.IsNullOrWhiteSpace(bodyPath))
        {
            var node=JsonNode.Parse(File.ReadAllText(bodyPath))!.AsObject();
            if(node["projectId"]!.GetValue<string>()!=project)throw new InvalidDataException("Wall body project mismatch");
            var rows=node["elements"]!.AsArray().Where(r=>r!=null && !index.ContainsKey(r["sourceGuid"]!.GetValue<string>())).Select(r=>r!.DeepClone()).ToArray();
            var sourceKeys=walls.Select(w=>w.SourceGuid).ToHashSet(StringComparer.OrdinalIgnoreCase);
            if(rows.Any(r=>!sourceKeys.Contains(r["sourceGuid"]!.GetValue<string>())))throw new InvalidDataException("Wall fallback contains foreign GUID");
            node["elements"]=new JsonArray(rows);string filtered=Path.Combine(output,"missing-wall-bodies.json");File.WriteAllText(filtered,node.ToJsonString());
            var result=ReferenceFallback.Run(doc,filtered,Path.GetFullPath,(n,total)=>progress?.Invoke("wallGeometry",n,total));report.Items.AddRange(result.Items);
        }
        // Exact IFC bodies take precedence over the nominal API sheet. Only
        // absent bodies may receive that explicitly incomplete substitute.
        var zero=ZeroThicknessWallStage.Run(doc,manifest,Path.Combine(output,"zero-width-walls"),progress);report.Items.AddRange(zero.Items);
        index=Storage.Index(doc,project);
        // Last resort allowed by the user: a nominal prism, never presented as
        // faithful profile geometry. Exact IFC bodies always take precedence.
        foreach(var record in walls.Where(w=>!index.ContainsKey(w.SourceGuid)))
        {
            using var t=new Transaction(doc,"Marked nominal wall substitute");
            try
            {
                t.Start();NativeGeometry.Configure(t,report,record.SourceGuid,"WallGeometry");var d=record.Raw.GetProperty("details");
                if(d.S("geometryType","Straight")!="Straight" || Math.Abs(d.D("arcAngle"))>1e-9)throw new InvalidDataException("Nominal straight prism unavailable");
                var g=new Geometry(doc,manifest.Coordinates.Transform);double z=d.D("zCoordinate"),h=d.D("height"),width=d.D("begThickness");
                var a=d.GetProperty("begCoordinate").Read<Point>();a.Z=z;var b=d.GetProperty("endCoordinate").Read<Point>();b.Z=z;
                var start=g.Xyz(a);var end=g.Xyz(b);var right=(end-start).Normalize().CrossProduct(XYZ.BasisZ);if(d.B("flipped"))right=right.Negate();
                if(!double.IsFinite(width)||!double.IsFinite(h)||width<=0||h<=0)throw new InvalidDataException("Invalid source nominal dimensions");
                double half=Geometry.Feet(width)/2;string line=d.S("referenceLineLocation","Center");
                double centre=line is "Outside" or "CoreOutside"?-half:line is "Inside" or "CoreInside"?half:0;
                centre+=Geometry.Feet(d.D("offset"));start+=right*centre;end+=right*centre;
                if(Geometry.Feet(width)<doc.Application.ShortCurveTolerance)
                {
                    XYZ[] sheet=[start,end,end+XYZ.BasisZ*Geometry.Feet(h),start+XYZ.BasisZ*Geometry.Feet(h)];
                    var surface=DirectShape.CreateElement(doc,new ElementId(BuiltInCategory.OST_Walls));surface.SetShape(ZeroThicknessWallStage.BuildSheet(sheet));
                    surface.ApplicationId="Ac2Revit.NominalWallFallback";surface.ApplicationDataId=record.SourceGuid;
                    surface.Name=Incomplete+" — приближённая поверхность тонкой стены";
                    surface.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)?.Set(Incomplete+": исходная толщина "+width.ToString("G9",System.Globalization.CultureInfo.InvariantCulture)+" м меньше геометрического допуска Revit. Только номинальная срединная поверхность; объём, слои, подрезки, проёмы и Room Bounding не перенесены. GUID "+record.SourceGuid);
                    surface.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)?.Set(record.Mark);
                    Storage.Tag(surface,project,record.SourceGuid,"nominal-wall-sheet:"+record.ContentHash,record.Raw.GetRawText());doc.Regenerate();ZeroThicknessWallStage.Verify(surface,sheet);
                    if(t.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Thin-wall surface rolled back");
                    report.Add(record.SourceGuid,"WallGeometry","approximate",Incomplete+": nominal mid-surface only; source thickness below Revit geometry tolerance, no volume or room-boundary guarantee",surface.UniqueId);continue;
                }
                var points=new[]{start+right*half,end+right*half,end-right*half,start-right*half};
                var loop=CurveLoop.Create(Enumerable.Range(0,4).Select(i=>(Curve)Line.CreateBound(points[i],points[(i+1)%4])).ToList());
                var solid=GeometryCreationUtilities.CreateExtrusionGeometry([loop],XYZ.BasisZ,Geometry.Feet(h));
                var shape=DirectShape.CreateElement(doc,new ElementId(BuiltInCategory.OST_Walls));shape.SetShape([solid]);
                shape.ApplicationId="Ac2Revit.NominalWallFallback";shape.ApplicationDataId=record.SourceGuid;
                shape.Name=Incomplete+" — приближённая номинальная геометрия";
                shape.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)?.Set(Incomplete+": номинальная призма; профиль, подрезки, слои и привязки не подтверждены. GUID "+record.SourceGuid);
                shape.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)?.Set(record.Mark);
                Storage.Tag(shape,project,record.SourceGuid,"nominal-wall:"+record.ContentHash,record.Raw.GetRawText());doc.Regenerate();
                if(t.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Nominal wall transaction failed");
                report.Add(record.SourceGuid,"WallGeometry","approximate",Incomplete+": nominal prism; exact source form unavailable",shape.UniqueId);
            }
            catch(Exception ex){if(t.GetStatus()==TransactionStatus.Started)t.RollBack();report.Add(record.SourceGuid,"WallGeometry","failed",ex.ToString());}
        }
        index=Storage.Index(doc,project);
        using(var t=new Transaction(doc,"Label incomplete wall representations"))
        {
            t.Start();NativeGeometry.Configure(t,report,"wall-labels","WallGeometry");foreach(var record in walls)
            {
                if(index.GetValueOrDefault(record.SourceGuid) is Wall native)
                {
                    var sourceType=manifest.Types.SingleOrDefault(typ=>typ.Key==record.Payload.S("typeKey"));
                    using var structure=native.WallType.GetCompoundStructure();
                    if(sourceType!=null && !sourceType.Layers.Any(l=>l.Function=="Core"))
                    {
                        bool technicalCore=Enumerable.Range(0,structure.LayerCount).Any(structure.IsCoreLayer);
                        string note=Incomplete+": конструкция Archicad без Core перенесена нативно. Толщины, материалы и функции слоёв сохранены; итоговые узлы/подрезки ещё требуют независимой сверки IFC. "+(technicalCore?"Revit использует техническую границу Core; несущая функция не назначалась.":"Revit принял пустую сердцевину.");
                        Annotate(native.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS),note);
                        Annotate(native.WallType.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_COMMENTS),note);
                        if(!native.WallType.Name.StartsWith(Incomplete))native.WallType.Name=Incomplete+" — "+native.WallType.Name;
                        report.Add(record.SourceGuid,"WallSemantics","incomplete",note,native.UniqueId);
                    }
                    continue;
                }
                if(index.GetValueOrDefault(record.SourceGuid) is not DirectShape shape || shape.ApplicationId is "Ac2Revit.NominalWallFallback" or ZeroThicknessWallStage.ApplicationId)continue;
                shape.Name=Incomplete+" — геометрия IFC";
                Annotate(shape.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS),Incomplete+": геометрия IFC; не нативная стена Revit. Нет редактируемых слоёв/привязок и гарантии Room Bounding. GUID "+record.SourceGuid+". Причина: "+record.Reason);
                report.Add(record.SourceGuid,"WallGeometry","incomplete",Incomplete+": IFC tessellation retained; native wall functionality unavailable",shape.UniqueId);
            }
            t.Commit();
        }
        int nativeCount=walls.Count(w=>index.GetValueOrDefault(w.SourceGuid) is Wall),substitutes=walls.Count(w=>index.GetValueOrDefault(w.SourceGuid) is DirectShape);
        var missing=walls.Where(w=>!index.ContainsKey(w.SourceGuid)).Select(w=>w.SourceGuid).ToArray();
        report.Add("wall-coverage","WallCoverage",missing.Length==0?"verified":"failed",$"Source {walls.Count}; native {nativeCount}; explicitly incomplete {substitutes}; missing {missing.Length}: "+string.Join(",",missing));
        report.Save(Path.Combine(output,"coverage.json"));
        if(missing.Length>0)throw new InvalidDataException("Wall coverage incomplete; see "+Path.Combine(output,"coverage.json"));
        return report;
    }
}
