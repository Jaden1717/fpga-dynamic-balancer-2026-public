"""Wall-clock observations around unchanged operations; never extra MMIO I/O."""
import time


class StageTimes:
    def __init__(self,clock=time.perf_counter):
        self.clock=clock
        self.started=clock()
        self.stages={}
        self.fpga_windows=[]

    def call(self,name,operation,*args,**kwargs):
        started=self.clock()
        try:
            return operation(*args,**kwargs)
        finally:
            item=self.stages.setdefault(name,dict(calls=0,wall_seconds=0.))
            item['calls']+=1
            item['wall_seconds']+=max(0.,self.clock()-started)

    def snapshot(self):
        return dict(total_wall_seconds=max(0.,self.clock()-self.started),
                    stages={k:dict(v) for k,v in self.stages.items()},
                    fpga_windows=list(self.fpga_windows),
                    semantics='Wall times include Python/MMIO overhead. Nested stages overlap; do not sum them. Result wait is not pure PL latency. Instrumentation overhead is included.')


class _ObservedMMIO:
    def __init__(self,inner,expected,clock):
        self.inner=inner
        self.expected=expected
        self.clock=clock
        self.accepted=0
        self.started=self.fed=self.valid=self.ack=None

    def read(self,offset):
        value=self.inner.read(offset)
        if offset==0x0c and self.fed is not None and self.valid is None and value&4:
            self.valid=self.clock()
        return value

    def write(self,offset,value):
        self.inner.write(offset,value)
        if offset==8 and value==1:
            self.started=self.clock()
        elif offset==0x14:
            self.accepted+=1
            if self.accepted==self.expected:self.fed=self.clock()
        elif offset==0x30 and value==1:
            self.ack=self.clock()

    def profile(self,completed):
        def span(first,last):
            return max(0.,last-first) if first is not None and last is not None else None
        return dict(completed=completed,input_words_observed=self.accepted,
                    input_feed_wall_seconds=span(self.started,self.fed),
                    result_wait_wall_seconds=span(self.fed,self.valid),
                    snapshot_ack_wall_seconds=span(self.valid,self.ack))


class TimedAccelerator:
    """Temporarily observe an existing driver's MMIO; preserve cleanup and errors."""
    def __init__(self,accelerator,record,*,clock=time.perf_counter):
        self.accelerator=accelerator
        self.record=record
        self.clock=clock

    def run(self,samples_raw,avg_log2,timeout=2.):
        words=list(samples_raw)
        original=self.accelerator.mmio
        observed=_ObservedMMIO(original,len(words),self.clock)
        completed=False
        self.accelerator.mmio=observed
        try:
            result=self.accelerator.run(words,avg_log2,timeout=timeout)
            completed=True
            return result
        finally:
            self.accelerator.mmio=original
            self.record(observed.profile(completed))
