from fastapi import Depends, HTTPException
from sqlalchemy import or_, select
from .models import Admin, Conversation, Lead
from .security import admin_user

ROLE_LABELS={'admin':'管理员','knowledge':'知识运营','support':'客服','advisor':'课程顾问'}
ROLES={
    'admin':{'staff.manage','knowledge.read','knowledge.write','knowledge.publish','conversations.read','conversations.handle','leads.read','leads.write','leads.assign','leads.export','analytics.read','settings.read','settings.write','audit.read'},
    'knowledge':{'knowledge.read','knowledge.write','settings.read'},
    'support':{'conversations.read','conversations.handle','leads.read','leads.write','analytics.read','settings.read'},
    'advisor':{'conversations.read','conversations.handle','leads.read','leads.write','analytics.read','settings.read'},
}

def has_permission(user,permission):
    return bool(user.is_active and permission in ROLES.get(user.role,set()))

def check_permission(user,permission):
    if not has_permission(user,permission):raise HTTPException(403,'当前账号没有此操作权限')

def require(permission):
    def dependency(user: Admin=Depends(admin_user)):
        check_permission(user,permission)
        return user
    return dependency

def user_data(user):
    return {'id':user.id,'name':user.name,'email':user.email,'role':user.role,
            'role_label':ROLE_LABELS.get(user.role,user.role),'is_active':user.is_active,
            'permissions':sorted(ROLES.get(user.role,set()))}

def scope_leads(query,user):
    query=query.where(Lead.tenant_id==user.tenant_id)
    return query.where(Lead.owner_id==user.id) if user.role=='advisor' else query

def scope_conversations(query,user):
    query=query.where(Conversation.tenant_id==user.tenant_id)
    if user.role=='advisor':
        owned=select(Lead.conversation_id).where(Lead.tenant_id==user.tenant_id,Lead.owner_id==user.id)
        query=query.where(or_(Conversation.id.in_(owned),Conversation.assigned_to_id==user.id))
    return query

def scoped_object(db,model,key,user):
    query=select(model).where(model.id==key,model.tenant_id==user.tenant_id)
    if model is Lead:query=scope_leads(query,user)
    if model is Conversation:query=scope_conversations(query,user)
    item=db.scalar(query)
    if not item:raise HTTPException(404,'记录不存在或不在授权范围内')
    return item
