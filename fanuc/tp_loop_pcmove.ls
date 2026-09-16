/PROG  PCMOVE
/ATTR
OWNER		= MNEDITOR;
COMMENT		= "PC manda indice, robo vai";
PROG_SIZE	= 0;
FILE_NAME	= ;
VERSION		= 0;
LINE_COUNT	= 15;
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
   1:  UFRAME_NUM=1 ;
   2:  UTOOL_NUM=1 ;
   3:  LBL[1] ;
   4:  WAIT (DI[1]=ON) ;
   5:  R[1]=GI[1] ;
   6:  IF (R[1]<1 OR R[1]>7),JMP LBL[2] ;
   7:J PR[R[1]] 30% FINE ;
   8:  LBL[2] ;
   9:  DO[1]=ON ;
  10:  WAIT (DI[1]=OFF) ;
  11:  DO[1]=OFF ;
  12:  JMP LBL[1] ;
/POS
/END
