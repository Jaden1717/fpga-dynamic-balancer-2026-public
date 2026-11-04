"""Generate reproducible independent math vectors for the 1x RTL scoreboard."""
from pathlib import Path
import json
import math
import random
from order1x_fixed import reference_result

def main():
    work=Path(__file__).resolve().parents[2]/'work/order1x_20261015'
    work.mkdir(parents=True,exist_ok=True)
    rng=random.Random(20261015)
    vectors=[]
    for k in [0,16,32,48]:
        for value in [1,-1,320,524287,-524288]:
            words=[0]*64;words[k]=value;vectors.append((0,words,'impulse'))
    for value in [0,1,-1,256000,-524288,524287]:vectors.append((2,[value]*256,'dc'))
    for avg in range(9):
        for phase in [0,37,179.9,-91]:
            amp=5120 if avg%2 else 200000
            dc=256000 if amp<10000 else 0
            words=[round(dc+amp*math.cos(2*math.pi*(i%64)/64+math.radians(phase))+.12*amp*math.cos(6*math.pi*(i%64)/64)) for i in range(64*(1<<avg))]
            vectors.append((avg,words,'harmonic'))
    for case in range(64):
        avg=case%7;words=[rng.randrange(-524288,524288) for _ in range(64*(1<<avg))]
        vectors.append((avg,words,'random_full_range'))
    references=[]
    with (work/'vectors.txt').open('w',encoding='ascii',newline='\n') as stream:
        for n,(avg,words,kind) in enumerate(vectors):
            result=reference_result(words,avg)
            stream.write('{} {} {} {} {}\n'.format(avg,result['real_q8'],result['imag_q8'],result['magnitude_q8'],result['phase_code']))
            stream.writelines(str(v)+'\n' for v in words)
            references.append(dict(case=n,avg_log2=avg,words=len(words),kind=kind,reference=result))
    (work/'references.json').write_text(json.dumps(references,indent=2)+'\n',encoding='utf-8')
    assert len(vectors)==126
    print('Independent cases:',len(vectors),'total samples:',sum(len(v[1]) for v in vectors))

if __name__=='__main__':main()
