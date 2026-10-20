using Autodesk.Revit.DB;
using Autodesk.Revit.DB.ExtensibleStorage;

namespace Ac2Revit;

public static class Storage
{
    static readonly Guid Id = new("01cc4a13-4361-4c9c-88a4-a54f807274ce");
    public static Schema Schema()
    {
        var existing = Autodesk.Revit.DB.ExtensibleStorage.Schema.Lookup(Id);
        if (existing != null) return existing;
        var builder = new SchemaBuilder(Id);
        builder.SetSchemaName("Ac2RevitSourceV1");
        builder.SetReadAccessLevel(AccessLevel.Public); builder.SetWriteAccessLevel(AccessLevel.Public);
        foreach (var name in new[] { "ProjectId", "SourceKey", "SourceHash", "RawJson" }) builder.AddSimpleField(name, typeof(string));
        return builder.Finish();
    }
    public static void Tag(Element e, string project, string key, string hash, string raw = "")
    {
        project=Guid.Parse(project).ToString("D");
        var schema = Schema(); var entity = new Entity(schema);
        entity.Set(schema.GetField("ProjectId"), project); entity.Set(schema.GetField("SourceKey"), key);
        entity.Set(schema.GetField("SourceHash"), hash); entity.Set(schema.GetField("RawJson"), raw);
        e.SetEntity(entity);
    }
    public static (string Project, string Key, string Hash)? Read(Element e)
    {
        var schema = Autodesk.Revit.DB.ExtensibleStorage.Schema.Lookup(Id);
        if (schema == null) return null;
        var entity = e.GetEntity(schema);
        return entity.IsValid() ? (entity.Get<string>(schema.GetField("ProjectId")), entity.Get<string>(schema.GetField("SourceKey")), entity.Get<string>(schema.GetField("SourceHash"))) : null;
    }
    public static Dictionary<string, Element> Index(Document doc, string project)
    {
        var result = new Dictionary<string, Element>(StringComparer.OrdinalIgnoreCase);
        var schema = Autodesk.Revit.DB.ExtensibleStorage.Schema.Lookup(Id);
        if (schema == null) return result;
        foreach (var e in new FilteredElementCollector(doc).WherePasses(new ExtensibleStorageFilter(Id)))
        {
            var tag = Read(e);
            if (tag.HasValue && string.Equals(tag.Value.Project,project,StringComparison.OrdinalIgnoreCase) && !result.TryAdd(tag.Value.Key, e))
                throw new InvalidOperationException("Duplicate imported source key in Revit: " + tag.Value.Key);
        }
        return result;
    }
}
