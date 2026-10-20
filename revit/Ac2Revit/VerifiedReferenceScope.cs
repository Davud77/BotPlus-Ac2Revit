using System.Security.Cryptography;

namespace Ac2Revit;

/// <summary>Hash a reference once while an open read lease prevents writes/replacement.</summary>
public sealed class VerifiedReferenceScope : IDisposable
{
    [ThreadStatic] static VerifiedReferenceScope? current;
    readonly VerifiedReferenceScope? previous;
    readonly Dictionary<string,(FileStream Stream,string Hash)> files=new(StringComparer.OrdinalIgnoreCase);
    bool disposed;
    public VerifiedReferenceScope(){previous=current;current=this;}

    public static void Verify(string path,string expectedHash)
    {
        if(current is null)
        {
            using var scope=new VerifiedReferenceScope();scope.Check(path,expectedHash);return;
        }
        current.Check(path,expectedHash);
    }
    void Check(string path,string expectedHash)
    {
        ObjectDisposedException.ThrowIf(disposed,this);
        string full=Path.GetFullPath(path);
        if(!files.TryGetValue(full,out var lease))
        {
            var stream=new FileStream(full,FileMode.Open,FileAccess.Read,FileShare.Read);
            try
            {
                lease=(stream,Convert.ToHexString(SHA256.HashData(stream)));
                if(!lease.Hash.Equals(expectedHash,StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("Reference file changed: "+full);
                files.Add(full,lease);
            }
            catch{stream.Dispose();throw;}
        }
        if(!lease.Hash.Equals(expectedHash,StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("Conflicting reference hash: "+full);
    }
    public void Dispose()
    {
        if(disposed)return;
        if(current!=this)throw new InvalidOperationException("Reference scopes must be disposed in nesting order");
        disposed=true;current=previous;
        foreach(var lease in files.Values)lease.Stream.Dispose();
        files.Clear();
    }
}
