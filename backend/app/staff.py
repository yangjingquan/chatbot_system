import re
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .db import get_db
from .models import Admin, AdminSession, Audit, Lead
from .permissions import require, user_data, ROLE_LABELS
from .security import passwords

router=APIRouter(prefix='/admin/staff')
Role=Literal['admin','knowledge','support','advisor']

class StaffCreate(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    email:str=Field(min_length=5,max_length=200)
    role:Role='advisor'
    password:str=Field(min_length=12,max_length=128)
    @field_validator('email')
    @classmethod
    def email_valid(cls,value):
        value=value.strip().lower()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value):raise ValueError('邮箱格式不正确')
        return value
    @field_validator('name')
    @classmethod
    def name_valid(cls,value):
        if not value.strip():raise ValueError('请输入姓名')
        return value.strip()

class StaffPatch(BaseModel):
    name:str|None=Field(None,min_length=1,max_length=100)
    role:Role|None=None
    is_active:bool|None=None

class PasswordInput(BaseModel):
    password:str=Field(min_length=12,max_length=128)

def audit(db,user,action,target):
    db.add(Audit(tenant_id=user.tenant_id,actor=user.name,action=action,target=target.email))

@router.get('')
def staff(user:Admin=Depends(require('staff.manage')),db:Session=Depends(get_db)):
    return [user_data(a) for a in db.scalars(select(Admin).where(Admin.tenant_id==user.tenant_id).order_by(Admin.name,Admin.id))]

@router.get('/options')
def options(user:Admin=Depends(require('leads.assign')),db:Session=Depends(get_db)):
    return [{'id':a.id,'name':a.name,'role':a.role,'role_label':ROLE_LABELS[a.role]} for a in db.scalars(select(Admin).where(Admin.tenant_id==user.tenant_id,Admin.is_active.is_(True),Admin.role.in_(['admin','advisor','support'])).order_by(Admin.name))]

@router.post('')
def create(data:StaffCreate,user:Admin=Depends(require('staff.manage')),db:Session=Depends(get_db)):
    employee=Admin(name=data.name,email=data.email,role=data.role,tenant_id=user.tenant_id,password_hash=passwords.hash(data.password),is_active=True)
    db.add(employee)
    try:db.flush()
    except IntegrityError:
        db.rollback();raise HTTPException(409,'此邮箱已有员工账号')
    audit(db,user,'创建员工',employee)
    db.commit()
    return user_data(employee)

def target_staff(db,key,user):
    employee=db.get(Admin,key)
    if not employee or employee.tenant_id!=user.tenant_id:raise HTTPException(404,'员工不存在')
    return employee

@router.patch('/{staff_id}')
def patch(staff_id:str,data:StaffPatch,user:Admin=Depends(require('staff.manage')),db:Session=Depends(get_db)):
    all_staff=db.scalars(select(Admin).where(Admin.tenant_id==user.tenant_id).order_by(Admin.id).with_for_update().execution_options(populate_existing=True)).all()
    employee=next((a for a in all_staff if a.id==staff_id),None)
    if not employee:raise HTTPException(404,'员工不存在')
    disabled=data.is_active is False or (data.role is not None and data.role!='admin')
    if employee.role=='admin' and employee.is_active and disabled and sum(a.role=='admin' and a.is_active for a in all_staff)<=1:
        raise HTTPException(409,'必须保留至少一名启用的管理员')
    revoke=(data.role is not None and data.role!=employee.role) or (data.is_active is not None and data.is_active!=employee.is_active)
    if data.name is not None:
        if not data.name.strip():raise HTTPException(422,'请输入姓名')
        employee.name=data.name.strip()
        db.execute(update(Lead).where(Lead.owner_id==employee.id,Lead.tenant_id==user.tenant_id).values(owner=employee.name))
    for key in ('role','is_active'):
        value=getattr(data,key)
        if value is not None:setattr(employee,key,value)
    if revoke:
        db.execute(delete(AdminSession).where(AdminSession.admin_id==employee.id))
    audit(db,user,'更新员工权限或状态',employee)
    db.commit()
    return user_data(employee)

@router.post('/{staff_id}/reset-password')
def reset_password(staff_id:str,data:PasswordInput,user:Admin=Depends(require('staff.manage')),db:Session=Depends(get_db)):
    employee=target_staff(db,staff_id,user)
    employee.password_hash=passwords.hash(data.password)
    db.execute(delete(AdminSession).where(AdminSession.admin_id==employee.id))
    audit(db,user,'重设员工密码并撤销登录',employee)
    db.commit()
    return {'ok':True}
