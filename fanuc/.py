from ftplib import FTP
import io, re
f=FTP(); f.connect('10.26.10.102',21,timeout=20); f.login('anonymous',''); f.cwd('md:')
b=io.BytesIO(); f.retrbinary('RETR syshost.sv', b.write); f.quit()
d=b.getvalue().decode('latin-1')
pat = re.compile(r'HTTP|AUTH|REALM|KCL|USER|PASSW', re.I)
achou=0
for linha in d.splitlines():
    if pat.search(linha):
        print(linha.strip()[:120]); achou+=1
        if achou>40: break
print('--- linhas no arquivo:', len(d.splitlines()))
