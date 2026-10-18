using System.Text.Json;
using System.Text.Json.Serialization;
using System.Security.Cryptography;

namespace Ac2Revit;

public static class Json
{
    public static readonly JsonSerializerOptions Options = new() { PropertyNameCaseInsensitive = true, WriteIndented = true };
    public static string Hash(object value) => Convert.ToHexString(SHA256.HashData(JsonSerializer.SerializeToUtf8Bytes(value, Options)));
    public static string S(this JsonElement j, string key, string fallback = "") => j.TryGetProperty(key, out var p) && p.ValueKind == JsonValueKind.String ? p.GetString() ?? fallback : fallback;
    public static double D(this JsonElement j, string key, double fallback = 0) => j.TryGetProperty(key, out var p) && p.ValueKind == JsonValueKind.Number ? p.GetDouble() : fallback;
    public static bool B(this JsonElement j, string key, bool fallback = false) => j.TryGetProperty(key, out var p) && p.ValueKind is JsonValueKind.True or JsonValueKind.False ? p.GetBoolean() : fallback;
    public static T Read<T>(this JsonElement j) => j.Deserialize<T>(Options) ?? throw new InvalidDataException("Missing data");
}
public sealed class Manifest
{
    public string SchemaVersion { get; set; } = "";
    public SourceInfo Source { get; set; } = new();
    public UnitInfo Units { get; set; } = new();
    public CoordinateInfo Coordinates { get; set; } = new();
    public List<LevelRecord> Levels { get; set; } = [];
    public List<JsonElement> Materials { get; set; } = [];
    public List<JsonElement> Surfaces { get; set; } = [];
    public List<TypeRecord> Types { get; set; } = [];
    public List<ElementRecord> Elements { get; set; } = [];
    public string ManifestHash { get; set; } = "";

    public static Manifest Load(string path)
    {
        using var stream = File.OpenRead(path);
        var m = JsonSerializer.Deserialize<Manifest>(stream, Json.Options) ?? throw new InvalidDataException("Empty manifest");
        m.Validate();
        return m;
    }
    public void Validate()
    {
        if (SchemaVersion != "1.0.0" || Units.Length != "m" || Units.Angle != "rad")
            throw new InvalidDataException("Expected schema 1.0.0, meters and radians");
        if (!Guid.TryParse(Source.ProjectId, out _)) throw new InvalidDataException("Invalid project UUID");
        Unique(Levels.Select(x => x.Key), "level");
        Unique(Types.Select(x => x.Key), "type");
        Unique(Elements.Select(x => x.SourceGuid), "element");
        if (Levels.Count == 0) throw new InvalidDataException("No levels");
        foreach (var l in Levels) if (!double.IsFinite(l.Elevation)) throw new InvalidDataException("Non-finite level");
        var ids = Elements.Select(x => x.SourceGuid).ToHashSet(StringComparer.OrdinalIgnoreCase);
        foreach (var e in Elements)
        {
            if (!Guid.TryParse(e.SourceGuid, out _)) throw new InvalidDataException("Invalid element GUID");
            if (e.Status != "unsupported" && !Levels.Any(x => x.Key == e.LevelKey)) throw new InvalidDataException("Missing home story: " + e.SourceGuid);
            if (e.Status != "unsupported" && e.Dependencies.Any(h => !ids.Contains(h))) throw new InvalidDataException("Missing host: " + e.SourceGuid);
        }
        foreach (var t in Types)
            if (t.Layers.Count == 0 || t.Layers.Any(l => !double.IsFinite(l.Width) || l.Width < 0 || (l.Width == 0 && l.Function != "Membrane")))
                throw new InvalidDataException("Invalid layers: " + t.Key);
        if (!double.IsFinite(Coordinates.Transform.RotationRadians) || !Coordinates.Transform.Translation.Finite)
            throw new InvalidDataException("Invalid transform");
        _ = OrderedElements();
    }
    static void Unique(IEnumerable<string> values, string label)
    {
        var set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var value in values) if (string.IsNullOrWhiteSpace(value) || !set.Add(value))
            throw new InvalidDataException("Duplicate/empty " + label + " key: " + value);
    }
    public List<ElementRecord> OrderedElements()
    {
        var dict = Elements.ToDictionary(e => e.SourceGuid, StringComparer.OrdinalIgnoreCase);
        var visiting = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var done = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var ordered = new List<ElementRecord>();
        void Visit(ElementRecord e)
        {
            if (done.Contains(e.SourceGuid)) return;
            if (!visiting.Add(e.SourceGuid)) throw new InvalidDataException("Dependency cycle");
            foreach (var host in e.Dependencies) if (dict.TryGetValue(host, out var h)) Visit(h);
            visiting.Remove(e.SourceGuid); done.Add(e.SourceGuid); ordered.Add(e);
        }
        foreach (var e in Elements.OrderBy(e => Stage(e.Kind)).ThenBy(e => e.SourceGuid, StringComparer.Ordinal)) Visit(e);
        return ordered;
    }
    static int Stage(string k) => k switch { "Grid" => -1, "Column" => 0, "Wall" => 1, "Beam" => 2, "Slab" => 3, "Door" or "Window" => 4, "Zone" => 9, _ => 5 };
}
public sealed class SourceInfo { public string ProjectId { get; set; } = ""; }
public sealed class UnitInfo { public string Length { get; set; } = ""; public string Angle { get; set; } = ""; }
public sealed class CoordinateInfo { public RigidTransform Transform { get; set; } = new(); }
public sealed class RigidTransform { public Point Translation { get; set; } = new(); public double RotationRadians { get; set; } }
public sealed class Point
{
    public double X { get; set; } public double Y { get; set; } public double Z { get; set; }
    [JsonIgnore] public bool Finite => double.IsFinite(X) && double.IsFinite(Y) && double.IsFinite(Z);
}
public sealed class Edge { public string Kind { get; set; } = "line"; public Point Start { get; set; } = new(); public Point End { get; set; } = new(); public Point? Mid { get; set; } }
public sealed class MeshRecord
{
    public List<Point> Vertices { get; set; } = [];
    public List<List<List<int>>> Faces { get; set; } = [];
    public bool Closed { get; set; }
}
public sealed class LevelRecord { public string Key { get; set; } = ""; public string Name { get; set; } = ""; public double Elevation { get; set; } }
public sealed class TypeRecord { public string Key { get; set; } = ""; public string Kind { get; set; } = ""; public string Name { get; set; } = ""; public List<LayerRecord> Layers { get; set; } = []; }
public sealed class LayerRecord { public double Width { get; set; } public string Function { get; set; } = ""; public string MaterialKey { get; set; } = ""; }
public sealed class ElementRecord
{
    public string SourceGuid { get; set; } = ""; public string Kind { get; set; } = ""; public string LevelKey { get; set; } = "";
    public string Mark { get; set; } = ""; public string Status { get; set; } = ""; public string Reason { get; set; } = "";
    public string ContentHash { get; set; } = ""; public JsonElement Payload { get; set; } public JsonElement Raw { get; set; }
    public List<string> Dependencies { get; set; } = [];
    public List<WallConnectionRecord> WallConnections { get; set; } = [];
}
public sealed class WallConnectionRecord
{
    public string TargetGuid { get; set; } = "";
    public string Relation { get; set; } = "";
    public int? OwnerEnd { get; set; }
    public bool TargetWithBeginPoint { get; set; }
}
public sealed class ImportConfig
{
    public bool AllowPartial { get; set; } = true;
    public string ZoneMode { get; set; } = "room";
    public double RoomAreaToleranceSquareMeters { get; set; } = 1.0;
    public bool ConnectWalls { get; set; } = true;
    public double WallJoinToleranceMeters { get; set; } = 0.001;
    public bool AutomaticSimpleColumns { get; set; }
    public string ColumnFamilyFile { get; set; } = "";
    public string ColumnWidthParameter { get; set; } = "b";
    public string ColumnDepthParameter { get; set; } = "h";
    public Dictionary<string, FamilyMapping> Families { get; set; } = new(StringComparer.OrdinalIgnoreCase);
    public static ImportConfig Load(string path)
    {
        var config = File.Exists(path) ? JsonSerializer.Deserialize<ImportConfig>(File.ReadAllText(path), Json.Options) ?? new() : new();
        if (!double.IsFinite(config.RoomAreaToleranceSquareMeters) || config.RoomAreaToleranceSquareMeters < 0)
            throw new InvalidDataException("Invalid room area tolerance");
        if (!double.IsFinite(config.WallJoinToleranceMeters) || config.WallJoinToleranceMeters <= 0 || config.WallJoinToleranceMeters > 0.01)
            throw new InvalidDataException("Wall join tolerance must be between 0 and 10 mm");
        return config;
    }
}
public sealed class FamilyMapping
{
    public string Family { get; set; } = ""; public string Type { get; set; } = "";
    public string WidthParameter { get; set; } = ""; public string HeightParameter { get; set; } = "";
    public string DepthParameter { get; set; } = "";
    public Point OriginOffset { get; set; } = new();
    public double RotationOffsetRadians { get; set; }
    public bool AllowMirroring { get; set; }
    public bool SymmetricSection { get; set; }
    public bool Calibrated { get; set; }
    public double SillFromWallBase { get; set; }
    public bool HandFlipXor { get; set; } public bool FacingFlipXor { get; set; }
}
public sealed class Report
{
    public string ProjectId { get; set; } = ""; public string Mode { get; set; } = "";
    public DateTime CreatedUtc { get; set; } = DateTime.UtcNow;
    public List<Outcome> Items { get; set; } = [];
    public List<ZoneAreaComparison> ZoneAreas { get; set; } = [];
    public List<WallEndJoinCheck> WallJoins { get; set; } = [];
    public List<WallOffsetPlacement> WallOffsets { get; set; } = [];
    public void Add(string key, string kind, string status, string message = "", string revitId = "") => Items.Add(new(key, kind, status, message, revitId));
    public void Save(string path) => File.WriteAllText(path, JsonSerializer.Serialize(this, Json.Options));
}
public sealed record Outcome(string SourceKey, string Kind, string Status, string Message, string RevitUniqueId);
public sealed record ZoneAreaComparison(string SourceGuid, double SourceAreaSquareMeters, double RevitAreaSquareMeters,
    double AbsoluteDifferenceSquareMeters, double ToleranceSquareMeters, bool Accepted, string Status);
public sealed record WallEndJoinCheck(string SourceGuid,int End,bool SourceBacked,List<string> NativeNeighbors,string Status);
public sealed record WallOffsetPlacement(string SourceGuid,string ReferenceLine,double ReferenceOffsetMeters,
    Point SourceStart,Point NativeStart,Point BodyMinimum,Point BodyMaximum);
