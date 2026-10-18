using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Structure;
using System.Text.Json;

namespace Ac2Revit;

/// <summary>Apply an explicit source-material decision to our own generated column families.</summary>
public static class ColumnBehaviorStage
{
    public sealed class Plan
    {
        public string Format {get;set;}="";
        public string ProjectId {get;set;}="";
        public List<Decision> Families {get;set;}=[];
    }
    public sealed class Decision
    {
        public string FamilyKey {get;set;}="";
        public string Behavior {get;set;}="";
        public string Evidence {get;set;}="";
        public List<string> SourceColumnGuids {get;set;}=[];
        public List<string> CoreMaterialGuids {get;set;}=[];
    }
    public static Report Run(Document doc,string input,string project,string output)
    {
        var plan=JsonSerializer.Deserialize<Plan>(File.ReadAllText(input),Json.Options)??throw new InvalidDataException("Empty column behavior plan");
        if(plan.Format!="ac2r.column-behavior/1" || plan.ProjectId!=project || plan.Families.Count==0)
            throw new InvalidDataException("Invalid column behavior plan/project");
        if(plan.Families.Select(f=>f.FamilyKey).Distinct(StringComparer.OrdinalIgnoreCase).Count()!=plan.Families.Count)
            throw new InvalidDataException("Duplicate behavior family key");
        var index=Storage.Index(doc,project);
        var instances=new FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_StructuralColumns).OfClass(typeof(FamilyInstance)).Cast<FamilyInstance>().ToList();
        var validated=new List<(Decision Decision,Family Family,List<FamilyInstance> Instances)>();
        var report=new Report{ProjectId=project,Mode="columnBehavior"};
        foreach(var decision in plan.Families)
        {
            if(decision.FamilyKey.Length!=64 || !decision.FamilyKey.All(Uri.IsHexDigit) || decision.Behavior!="Concrete" || string.IsNullOrWhiteSpace(decision.Evidence)
                || decision.SourceColumnGuids.Count==0 || decision.CoreMaterialGuids.Count!=1 || !Guid.TryParse(decision.CoreMaterialGuids[0],out _)
                || decision.SourceColumnGuids.Any(g=>!Guid.TryParse(g,out _))
                || decision.SourceColumnGuids.Distinct(StringComparer.OrdinalIgnoreCase).Count()!=decision.SourceColumnGuids.Count)
                throw new InvalidDataException("Unverified/unsupported column behavior decision: "+decision.FamilyKey);
            string key="profile-family:"+decision.FamilyKey;
            if(!index.TryGetValue(key,out var stored)){report.Add(key,"ColumnBehavior","notPresent","Generated family absent; no existing elements changed");continue;}
            if(stored is not Family family || family.FamilyCategory.Id!=new ElementId(BuiltInCategory.OST_StructuralColumns))
                throw new InvalidDataException("Source family key is not a structural column family: "+key);
            var members=instances.Where(i=>i.Symbol.Family.Id==family.Id).ToList();
            var expected=decision.SourceColumnGuids.ToHashSet(StringComparer.OrdinalIgnoreCase);
            if(members.Any(i=>Storage.Read(i) is not { } tag || tag.Project!=project || !expected.Contains(tag.Key)))
                throw new InvalidDataException("Family contains foreign/unverified instances: "+family.Name);
            // A failed candidate may remain a mesh; only native members are changed.
            foreach(string guid in expected)
                if(index.GetValueOrDefault(guid) is FamilyInstance native && native.Symbol.Family.Id!=family.Id)
                    throw new InvalidDataException("Source column belongs to a different native family: "+guid);
            var p=family.get_Parameter(BuiltInParameter.FAMILY_STRUCT_MATERIAL_TYPE);
            if(p==null || p.IsReadOnly || p.StorageType!=StorageType.Integer)
                throw new InvalidOperationException("Family behavior is not writable: "+family.Name);
            validated.Add((decision,family,members));
        }
        Directory.CreateDirectory(output);
        // Full snapshots include neighbouring walls: concrete behavior can affect joins.
        GeometryAudit.Write(doc,Path.Combine(output,"columns-before.jsonl"),null,category:BuiltInCategory.OST_StructuralColumns);
        GeometryAudit.Write(doc,Path.Combine(output,"walls-before.jsonl"),null);
        var readback=new List<object>();
        using(var t=new Transaction(doc,"Source-verified structural column behavior"))
        {
            t.Start();
            foreach(var row in validated)
            {
                var parameter=row.Family.get_Parameter(BuiltInParameter.FAMILY_STRUCT_MATERIAL_TYPE);
                int before=parameter.AsInteger();
                if(before!=(int)StructuralMaterialType.Concrete && !parameter.Set((int)StructuralMaterialType.Concrete))
                    throw new InvalidOperationException("Concrete behavior assignment failed: "+row.Family.Name);
                doc.Regenerate();
                if(row.Family.StructuralMaterialType!=StructuralMaterialType.Concrete)
                    throw new InvalidOperationException("Concrete behavior readback mismatch: "+row.Family.Name);
                foreach(var instance in row.Instances)
                {
                    var bounding=instance.get_Parameter(BuiltInParameter.WALL_ATTR_ROOM_BOUNDING);
                    // Preserve any deliberately disabled flag; the new concrete default is read back.
                    readback.Add(new{sourceGuid=Storage.Read(instance)!.Value.Key,elementId=instance.Id.Value,familyKey=row.Decision.FamilyKey,
                        previousBehavior=before,behavior=row.Family.StructuralMaterialType.ToString(),roomBounding=bounding?.AsInteger()});
                }
                report.Add("profile-family:"+row.Decision.FamilyKey,"ColumnBehavior",before==(int)StructuralMaterialType.Concrete?"unchanged":"updated",
                    $"Concrete; native members={row.Instances.Count}; source core material={row.Decision.CoreMaterialGuids[0]}; geometry acceptance is separate",row.Family.UniqueId);
            }
            if(t.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Column behavior transaction was not committed");
        }
        File.WriteAllText(Path.Combine(output,"instances.json"),JsonSerializer.Serialize(readback,Json.Options));
        GeometryAudit.Write(doc,Path.Combine(output,"columns-after.jsonl"),null,category:BuiltInCategory.OST_StructuralColumns);
        GeometryAudit.Write(doc,Path.Combine(output,"walls-after.jsonl"),null);
        return report;
    }
}
