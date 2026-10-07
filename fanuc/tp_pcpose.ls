/PROG  PCPOSE
/ATTR
OWNER		= MNEDITOR;
COMMENT		= "PC manda 6 juntas por R1";
PROG_SIZE	= 0;
FILE_NAME	= ;
VERSION		= 0;
LINE_COUNT	= 38;
MEMORY_SIZE	= 0;
PROTECT		= READ_WRITE;
TCD:  STACK_SIZE	= 0,
      TASK_PRIORITY	= 50,
      TIME_SLICE	= 0,
      BUSY_LAMP_OFF	= 0,
      ABORT_REQUEST	= 0,
      PAUSE_REQUEST	= 0;
DEFAULT_GROUP	= 1,*,*,*,*;
CONTROL_CODE	= 00000000 00000000;
/MN
   1:  LBL[1] ;
   2:  PR[50]=JPOS ;
   3:  R[1]=9000 ;
   4:  WAIT DI[10]=ON ;
   5:  R[61]=R[1]/10 ;
   6:  PR[50,1]=R[61]-400 ;
   7:  R[1]=9001 ;
   8:  WAIT DI[10]=OFF ;
   9:  WAIT DI[10]=ON ;
  10:  R[61]=R[1]/10 ;
  11:  PR[50,2]=R[61]-400 ;
  12:  R[1]=9002 ;
  13:  WAIT DI[10]=OFF ;
  14:  WAIT DI[10]=ON ;
  15:  R[61]=R[1]/10 ;
  16:  PR[50,3]=R[61]-400 ;
  17:  R[1]=9003 ;
  18:  WAIT DI[10]=OFF ;
  19:  WAIT DI[10]=ON ;
  20:  R[61]=R[1]/10 ;
  21:  PR[50,4]=R[61]-400 ;
  22:  R[1]=9004 ;
  23:  WAIT DI[10]=OFF ;
  24:  WAIT DI[10]=ON ;
  25:  R[61]=R[1]/10 ;
  26:  PR[50,5]=R[61]-400 ;
  27:  R[1]=9005 ;
  28:  WAIT DI[10]=OFF ;
  29:  WAIT DI[10]=ON ;
  30:  R[61]=R[1]/10 ;
  31:  PR[50,6]=R[61]-400 ;
  32:  R[1]=9006 ;
  33:  WAIT DI[10]=OFF ;
  34:  WAIT DI[9]=ON ;
  35:J PR[50] 10% FINE ;
  36:  R[1]=9100 ;
  37:  WAIT DI[9]=OFF ;
  38:  JMP LBL[1] ;
/POS
/END
! ------------------------------------------------------------------
! Nao carrega por FTP (falta R507): digitar no pendant.
! Seis blocos iguais, desenrolados: o pendant nao aceita registrador no
! indice j de PR[i,j]. Digitar o bloco da junta 1 e usar EDCMD Copy/Paste.
!
! Protocolo (pose_fanuc.py e o outro lado):
!   R[1]=9000       pronto, esperando a junta 1
!   PC escreve R[1]=(junta+400)*10, liga DI[10]
!   robo grava PR[50,n], responde R[1]=9000+n, espera DI[10] OFF
!   depois da 6a: PC confere PR[50] no posreg.va, liga DI[9]
!   robo move, responde R[1]=9100, espera DI[9] OFF, volta ao LBL[1]
!
! R[1] e obrigatorio: e o unico registrador exposto por CIP (R[2], R[60],
! R[100] dao "instance undefined"). Ele tem nome 'ASA' -- combinar com o dono.
! +400: o robo le o INT16 do CIP sem sinal (-1700 chega como 63836).
! R[61] temporario, PR[50] alvo. R[2..31] e PR[1..27] sao de outras pessoas.
! PR[50]=JPOS na linha 2 ja deixa o PR em representacao de junta.
