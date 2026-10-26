"""Database-side eligible candidate selection and ranking, independent of corpus order."""
import re
from sqlalchemy import select, func, literal_column, or_, case
from .models import Chunk, Document, now

def search_documents(db,query,public_only,tenant_id,limit,context):
    from .document_service import infer_context, REGIONS
    from . import knowledge as k
    scope=context if context is not None else infer_context(query)
    tokens=sorted(set(k.terms(query)))[:80]
    if not tokens:return []
    base=select(Chunk,Document).join(Document,Chunk.document_id==Document.id).where(
        Chunk.tenant_id==tenant_id,Document.tenant_id==tenant_id,Document.status=='published',
        or_(Document.valid_from.is_(None),Document.valid_from<=now()),or_(Document.valid_until.is_(None),Document.valid_until>=now()),
        or_(Document.exam_year.is_(None),Document.exam_year==scope.get('exam_year')),
        or_(Document.exam_type.in_(['通用','']),Document.exam_type==scope.get('exam_type')))
    normalized=case(*[(Document.region.contains(r,autoescape=True),r) for r in REGIONS],else_=Document.region)
    base=base.where(or_(Document.region.in_(['全国','通用','']),normalized==scope.get('region')))
    if public_only:base=base.where(Document.visibility=='public')
    pg=db.bind.dialect.name=='postgresql'
    if pg:
        # Only word characters enter the tsquery grammar; values remain bound parameters.
        safe=[re.sub(r'[^\w\u4e00-\u9fff]','',t) for t in tokens]
        safe=[t for t in safe if t]
        if not safe:return []
        vector=func.to_tsvector(literal_column("'simple'"),Chunk.terms)
        tsquery=func.to_tsquery(literal_column("'simple'"),' | '.join(safe))
        score=func.ts_rank_cd(vector,tsquery)
        lexical=db.execute(base.where(vector.op('@@')(tsquery)).order_by(score.desc(),Chunk.id).limit(60)).all()
    else:
        # SQLite is the test/development fallback: ranking/filtering still happens in SQL.
        matches=[Chunk.terms.contains(t,autoescape=True) for t in tokens]
        score=sum(case((match,1),else_=0) for match in matches)
        lexical=db.execute(base.where(or_(*matches)).order_by(score.desc(),Chunk.id).limit(60)).all()
    semantic=[]
    if pg and k._model is not None:
        with k._model_lock:embedding=next(iter(k._model.query_embed(query))).tolist()
        distance=Chunk.embedding.cosine_distance(embedding)
        semantic=db.execute(base.where(Chunk.embedding.is_not(None),distance<=.40).order_by(distance,Chunk.id).limit(30)).all()
    scores={};items={}
    for rows in (lexical,semantic):
        for i,(chunk,doc) in enumerate(rows):
            scores[chunk.id]=scores.get(chunk.id,0)+1/(60+i)
            items[chunk.id]=(chunk,doc)
    result=[]
    for key in sorted(scores,key=lambda key:(-scores[key],key))[:limit]:
        c,d=items[key]
        result.append({'chunk_id':c.id,'document_id':d.id,'title':d.title,'category':d.category,'text':c.text,'version':d.version,'is_demo':d.is_demo,'source_url':d.source_url,'knowledge_type':d.knowledge_type,'exam_type':d.exam_type,'exam_year':d.exam_year,'region':d.region,'score':round(scores[key],5)})
    return result
