"""Exact source identity lookup. Never uses names, proximity or bounding boxes."""
from collections import defaultdict


class SourceIfcIndex:
    def __init__(self, manifest, model):
        self.products=defaultdict(list)
        self.tags=defaultdict(list)
        self.sources={e['sourceGuid'].upper():e for e in manifest['elements']}
        self.reverse={}
        for product in model.by_type('IfcProduct'):
            self.products[product.GlobalId].append(product)
            tag=getattr(product,'Tag',None)
            if tag:self.tags[tag.upper()].append(product)
        for key,e in self.sources.items():
            identifiers=e.get('raw',{}).get('ifcIdentifiers',{})
            for field in ('ifcId','externalIFCId'):
                value=identifiers.get(field)
                if value:
                    if value in self.reverse and self.reverse[value]!=key:
                        raise ValueError('Two source GUIDs share the same explicit IFC identifier: '+value)
                    self.reverse[value]=key

    def matches(self, element):
        key=element['sourceGuid'].upper()
        identifiers=element.get('raw',{}).get('ifcIdentifiers',{})
        for field in ('ifcId','externalIFCId'):
            value=identifiers.get(field)
            if value and self.products.get(value):
                rows=self.products[value]
                if any(getattr(r,'Tag',None) and r.Tag.upper() in self.sources and r.Tag.upper()!=key for r in rows):
                    raise ValueError('Explicit IFC ID conflicts with source GUID stored in Tag')
                return rows
        rows=self.tags.get(key,[])
        if any(r.GlobalId in self.reverse and self.reverse[r.GlobalId]!=key for r in rows):
            raise ValueError('IFC Tag conflicts with explicit source IFC ID')
        return rows

    def source_guid(self, product):
        identity=self.reverse.get(product.GlobalId)
        tag=getattr(product,'Tag',None)
        tag=tag.upper() if tag and tag.upper() in self.sources else None
        if identity and tag and identity!=tag:raise ValueError('Conflicting host identity')
        return identity or tag or ''
