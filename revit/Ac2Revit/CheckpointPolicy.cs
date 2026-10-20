namespace Ac2Revit;

/// <summary>A reopen is useful only after committed progress and memory growth.</summary>
public static class CheckpointPolicy
{
    public static bool ShouldReopen(bool committed,long initialBytes,long currentBytes) =>
        committed && (currentBytes-initialBytes>768L*1024*1024 ||
            initialBytes<=8L*1024*1024*1024 && currentBytes>8L*1024*1024*1024);
}
