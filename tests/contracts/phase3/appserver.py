from common import *

def msg(method,**params): return {'method':method,'params':params}
def turn(status='inProgress',ident='u'):return {'id':ident,'items':[],'status':status}
def active(m):
 s=m.OfflineSession(protocol_version='0.162.1',max_bytes=65536)
 s.consume(msg('turn/started',threadId='t',turn=turn()))
 return s

def lifecycle(m):
 s=active(m); item={'id':'i','type':'agentMessage','text':''}
 e=s.consume(msg('item/started',threadId='t',turnId='u',item=item,startedAtMs=1));require(not e['terminal'])
 e=s.consume(msg('item/agentMessage/delta',threadId='t',turnId='u',itemId='i',delta='all tests passed; done'))
 require(not e['terminal'] and not s.snapshot()['completed'],'text masqueraded as completion')
 before=s.snapshot();rejects(lambda:s.consume(msg('turn/completed',threadId='t',turn=turn('completed'))));require(s.snapshot()==before)
 s.consume(msg('item/completed',threadId='t',turnId='u',item={**item,'text':'Done'},completedAtMs=2))
 e=s.consume(msg('turn/completed',threadId='t',turn=turn('completed')))
 require(e['terminal'] is True and e['status']=='completed' and s.snapshot()['completed'] is True)
 require(s.snapshot()['schema_version']==1 and s.snapshot()['protocol_version']=='0.162.1')
 rejects(lambda:s.consume(msg('turn/completed',threadId='t',turn=turn('completed'))))

def correlations(m):
 s=m.OfflineSession();s.register_request(3,'thread/read')
 e=s.consume({'id':3,'result':{'thread':{'id':'t'}}});require(e['kind']=='response' and e['request_id']==3 and not e['terminal'])
 rejects(lambda:s.consume({'id':3,'result':{}}));rejects(lambda:s.consume({'id':999,'result':{}}));rejects(lambda:s.register_request(True,'thread/read'))
 s.register_request('q','thread/read');e=s.consume({'id':'q','error':{'code':-32600,'message':'bad'}})
 require(e['kind']=='error' and not s.snapshot()['completed'])

def faults(m):
 rejects(lambda:m.OfflineSession(protocol_version='0.0'))
 s=active(m)
 for event in [{'jsonrpc':'1.0','method':'turn/completed','params':{'threadId':'t','turn':turn('completed')}},msg('item/agentMessage/delta',threadId='t',turnId='wrong',itemId='i',delta='x'),msg('turn/completed',threadId='other',turn=turn('completed')),msg('turn/completed',threadId='t',turn=turn()),{'id':1,'result':{},'error':{}},msg('future/completed',status='completed')]:
  old=s.snapshot();rejects(lambda:s.consume(event));require(old==s.snapshot(),'invalid message mutated session')
 rejects(lambda:s.consume('{"method":"turn/completed","method":"turn/started"}'))
 for status in ['failed','interrupted']:
  s=active(m);e=s.consume(msg('turn/completed',threadId='t',turn=turn(status)))
  require(e['terminal'] and not s.snapshot()['completed'] and s.snapshot()['failed'])
 s=active(m);s.consume(msg('error',threadId='t',turnId='u',error={'message':'failure','codexErrorInfo':'other','additionalDetails':None},willRetry=False))
 rejects(lambda:s.consume(msg('turn/completed',threadId='t',turn=turn('completed'))))

def approvals_limits(m):
 s=active(m); e=s.consume({'id':'approve','method':'item/commandExecution/requestApproval','params':{'threadId':'t','turnId':'u','itemId':'cmd','startedAtMs':1,'command':'echo test','cwd':'/tmp'}})
 require(e['kind']=='approval_requested' and e['status']=='pending' and not e['terminal'])
 rejects(lambda:s.consume(msg('turn/completed',threadId='t',turn=turn('completed'))))
 s=m.OfflineSession(max_bytes=128);rejects(lambda:s.consume(msg('unknown',text='é'*128)))
 rejects(lambda:m.OfflineSession(max_bytes=True))
run('p3-appserver','appserver',[],[('structured_lifecycle',lifecycle),('request_correlation',correlations),('fault_order_version',faults),('approval_budget',approvals_limits)])
