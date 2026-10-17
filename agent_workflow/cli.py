import argparse
import json
import sys
from .core import Engine, WorkflowError, _capability_inventory


def dispatch(engine, action, args):
    methods = {**{name: name for name in ('scope_status','scope_preflight','scope_repair','next_step','capabilities','uiux_init','uiux_discover','uiux_evidence','uiux_review','uiux_query','context_query','handoff_build','handoff_read','experience_record','experience_query','producer_init','producer_offer','producer_scan','producer_discover','queue_status')}, 'import': 'import_task', 'claim': 'claim', 'heartbeat': 'heartbeat', 'progress': 'progress',
               'change': 'change', 'verify': 'verify', 'transition': 'transition', 'status': 'status', 'history': 'history', 'fail': 'fail'}
    if action not in methods:
        raise WorkflowError('unknown action')
    return getattr(engine, methods[action])(**args)


def main():
    p = argparse.ArgumentParser(description='Evidence-driven local AI pull workflows')
    p.add_argument('--state', required=True, help='shared local state directory')
    p.add_argument('--config', help='initial project JSON; frozen after initialization')
    p.add_argument('action', choices=['scope_status','scope_preflight','scope_repair','next_step', 'capabilities', 'init', 'import', 'claim', 'heartbeat', 'progress', 'change', 'verify', 'transition', 'status', 'history', 'fail', 'mcp', 'producer_init', 'producer_offer', 'producer_scan', 'producer_discover', 'queue_status', 'experience_record', 'experience_query', 'handoff_build', 'handoff_read', 'context_query', 'uiux_init', 'uiux_discover', 'uiux_evidence', 'uiux_review', 'uiux_query'])
    p.add_argument('--args', default='{}', help='JSON object of action arguments')
    ns = p.parse_args()
    try:
        if ns.action == 'capabilities':
            args = json.loads(ns.args)
            if not isinstance(args, dict) or args:
                raise WorkflowError('capabilities requires an empty argument object')
            print(json.dumps({'ok': True, 'result': _capability_inventory()}, ensure_ascii=False))
            return
        if ns.action == 'next_step':
            from .guidance import next_step
            from .core import digest
            from pathlib import Path
            args = json.loads(ns.args)
            if not isinstance(args, dict) or set(args)-{'task_id','run_id'}:
                raise WorkflowError('invalid or unavailable guidance request')
            result = next_step(ns.state, digest(Path(__file__).parent), **args)
            print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
            return
        config = json.load(open(ns.config)) if ns.config else None
        engine = Engine(ns.state, config)
        if ns.action == 'mcp':
            from .mcp import serve
            serve(engine)
            return
        result = {'initialized': True, 'workflow': engine.workflow} if ns.action == 'init' else dispatch(engine, ns.action, json.loads(ns.args))
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
    except (WorkflowError, TypeError, OSError, json.JSONDecodeError) as e:
        print(json.dumps({'ok': False, 'error': str(e)}), file=sys.stderr if ns.action == 'mcp' else sys.stdout)
        sys.exit(1)


if __name__ == '__main__':
    main()
