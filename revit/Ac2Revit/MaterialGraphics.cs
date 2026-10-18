using Autodesk.Revit.DB;

namespace Ac2Revit;

/// <summary>Surface shading colours only. No texture or cut-pattern approximation.</summary>
public static class MaterialGraphics
{
    public static void Apply(Document doc,Manifest manifest,Report report)
    {
        var index=Storage.Index(doc,manifest.Source.ProjectId);
        var surfaces=manifest.Surfaces.Where(s=>s.TryGetProperty("index",out _)).GroupBy(s=>s.D("index")).ToDictionary(g=>g.Key,g=>g.Single());
        int changed=0,missing=0;
        using var t=new Transaction(doc,"Archicad surface shading colours");t.Start();
        foreach(var raw in manifest.Materials)
        {
            if(!raw.TryGetProperty("attributeId",out var attribute))continue;
            string key="material:"+attribute.S("guid").ToUpperInvariant();
            if(index.GetValueOrDefault(key) is not Material material || !surfaces.TryGetValue(raw.D("cutSurfaceIndex",-1),out var surface) || !surface.TryGetProperty("surfaceColor",out var colour))
            {missing++;continue;}
            byte Channel(string channel)
            {
                double value=colour.D(channel,double.NaN);
                if(!double.IsFinite(value) || value<0 || value>1)throw new InvalidDataException("Invalid Archicad RGB channel for "+key);
                return (byte)Math.Round(value*255,MidpointRounding.AwayFromZero);
            }
            material.Color=new Color(Channel("red"),Channel("green"),Channel("blue"));
            double transparency=surface.D("transparency");
            if(!double.IsFinite(transparency) || transparency<0 || transparency>100)throw new InvalidDataException("Invalid transparency for "+key);
            material.Transparency=(int)Math.Round(transparency);changed++;
        }
        if(t.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Material graphics transaction rolled back");
        report.Add("material-graphics","MaterialGraphics","updated",$"Shading RGB/transparency assigned for {changed} building materials; {missing} missing surface mappings. Appearance textures, face overrides and cut patterns are not imported.");
    }
}
