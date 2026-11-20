from importlib import import_module
from time import perf_counter
from app.config import STAGES, DEFAULTS, SAMPLES
from app.manifest import make
from app.normalize import serial
from app.errors import DomainError
from app.access import scoped_view
from app import store

SCOPE_KEYS = ("role", "ward", "scope")


def _clean_params(params):
    """Remove access-scope keys so they never enter the run id / manifest.

    The scope only changes what a caller is allowed to see, it must not change
    the batch identity or the computation.
    """
    if not isinstance(params, dict):
        return params
    return {key: value for key, value in params.items() if key not in SCOPE_KEYS}


def run(raw=None, params=None, stop=7, persist=True, role=None, ward=None, scope=None):
    if raw is None: raw=(SAMPLES/"dirty.csv").read_bytes()
    if isinstance(raw,str): raw=raw.encode("utf-8")
    if not isinstance(params,(dict,type(None))): raise DomainError("PARAMS","参数必须是对象")
    actual={**DEFAULTS,**(_clean_params(params) or {})}
    t=perf_counter();ctx={"source":raw,"params":actual,"stages":[],"quarantine":[]}
    for stage in range(1,stop+1):
        mod=import_module(f"app.modules.m{stage}_{STAGES[stage-1]}.service")
        ctx=mod.execute(ctx)
    ctx.pop("source",None);ctx.pop("raw",None)
    result=serial(ctx);result["manifest"]=make(raw,actual);result["elapsed_ms"]=round((perf_counter()-t)*1000,3)
    result["version"]=1;result["review"]="待复核"
    if persist and stop==7: result=store.save(result)
    if role is not None or ward is not None or scope is not None:
        return scoped_view(result, role=role, ward=ward, scope=scope)
    return result

