.386
.model flat
option casemap:none

EXTERN _g_winmm_exports:DWORD

.code
PUBLIC WinMMOrdinal1
WinMMOrdinal1 PROC
    xor eax, eax
    ret
WinMMOrdinal1 ENDP

PUBLIC WinMMOrdinal2
WinMMOrdinal2 PROC
    jmp DWORD PTR [_g_winmm_exports + 0]
WinMMOrdinal2 ENDP

PUBLIC WinMMOrdinal3
WinMMOrdinal3 PROC
    jmp DWORD PTR [_g_winmm_exports + 4]
WinMMOrdinal3 ENDP

PUBLIC WinMMOrdinal4
WinMMOrdinal4 PROC
    jmp DWORD PTR [_g_winmm_exports + 8]
WinMMOrdinal4 ENDP

PUBLIC WinMMOrdinal5
WinMMOrdinal5 PROC
    jmp DWORD PTR [_g_winmm_exports + 12]
WinMMOrdinal5 ENDP

PUBLIC WinMMOrdinal6
WinMMOrdinal6 PROC
    jmp DWORD PTR [_g_winmm_exports + 16]
WinMMOrdinal6 ENDP

PUBLIC WinMMOrdinal7
WinMMOrdinal7 PROC
    jmp DWORD PTR [_g_winmm_exports + 20]
WinMMOrdinal7 ENDP

PUBLIC WinMMOrdinal8
WinMMOrdinal8 PROC
    jmp DWORD PTR [_g_winmm_exports + 24]
WinMMOrdinal8 ENDP

PUBLIC WinMMOrdinal9
WinMMOrdinal9 PROC
    jmp DWORD PTR [_g_winmm_exports + 28]
WinMMOrdinal9 ENDP

PUBLIC WinMMOrdinal10
WinMMOrdinal10 PROC
    jmp DWORD PTR [_g_winmm_exports + 32]
WinMMOrdinal10 ENDP

PUBLIC WinMMOrdinal11
WinMMOrdinal11 PROC
    jmp DWORD PTR [_g_winmm_exports + 36]
WinMMOrdinal11 ENDP

PUBLIC WinMMOrdinal12
WinMMOrdinal12 PROC
    jmp DWORD PTR [_g_winmm_exports + 40]
WinMMOrdinal12 ENDP

PUBLIC WinMMOrdinal13
WinMMOrdinal13 PROC
    jmp DWORD PTR [_g_winmm_exports + 44]
WinMMOrdinal13 ENDP

PUBLIC WinMMOrdinal14
WinMMOrdinal14 PROC
    jmp DWORD PTR [_g_winmm_exports + 48]
WinMMOrdinal14 ENDP

PUBLIC WinMMOrdinal15
WinMMOrdinal15 PROC
    jmp DWORD PTR [_g_winmm_exports + 52]
WinMMOrdinal15 ENDP

PUBLIC WinMMOrdinal16
WinMMOrdinal16 PROC
    jmp DWORD PTR [_g_winmm_exports + 56]
WinMMOrdinal16 ENDP

PUBLIC WinMMOrdinal17
WinMMOrdinal17 PROC
    jmp DWORD PTR [_g_winmm_exports + 60]
WinMMOrdinal17 ENDP

PUBLIC WinMMOrdinal18
WinMMOrdinal18 PROC
    jmp DWORD PTR [_g_winmm_exports + 64]
WinMMOrdinal18 ENDP

PUBLIC WinMMOrdinal19
WinMMOrdinal19 PROC
    jmp DWORD PTR [_g_winmm_exports + 68]
WinMMOrdinal19 ENDP

PUBLIC WinMMOrdinal20
WinMMOrdinal20 PROC
    jmp DWORD PTR [_g_winmm_exports + 72]
WinMMOrdinal20 ENDP

PUBLIC WinMMOrdinal21
WinMMOrdinal21 PROC
    jmp DWORD PTR [_g_winmm_exports + 76]
WinMMOrdinal21 ENDP

PUBLIC WinMMOrdinal22
WinMMOrdinal22 PROC
    jmp DWORD PTR [_g_winmm_exports + 80]
WinMMOrdinal22 ENDP

PUBLIC WinMMOrdinal23
WinMMOrdinal23 PROC
    jmp DWORD PTR [_g_winmm_exports + 84]
WinMMOrdinal23 ENDP

PUBLIC WinMMOrdinal24
WinMMOrdinal24 PROC
    jmp DWORD PTR [_g_winmm_exports + 88]
WinMMOrdinal24 ENDP

PUBLIC WinMMOrdinal25
WinMMOrdinal25 PROC
    jmp DWORD PTR [_g_winmm_exports + 92]
WinMMOrdinal25 ENDP

PUBLIC WinMMOrdinal26
WinMMOrdinal26 PROC
    jmp DWORD PTR [_g_winmm_exports + 96]
WinMMOrdinal26 ENDP

PUBLIC WinMMOrdinal27
WinMMOrdinal27 PROC
    jmp DWORD PTR [_g_winmm_exports + 100]
WinMMOrdinal27 ENDP

PUBLIC WinMMOrdinal28
WinMMOrdinal28 PROC
    jmp DWORD PTR [_g_winmm_exports + 104]
WinMMOrdinal28 ENDP

PUBLIC WinMMOrdinal29
WinMMOrdinal29 PROC
    jmp DWORD PTR [_g_winmm_exports + 108]
WinMMOrdinal29 ENDP

PUBLIC WinMMOrdinal30
WinMMOrdinal30 PROC
    jmp DWORD PTR [_g_winmm_exports + 112]
WinMMOrdinal30 ENDP

PUBLIC WinMMOrdinal31
WinMMOrdinal31 PROC
    jmp DWORD PTR [_g_winmm_exports + 116]
WinMMOrdinal31 ENDP

PUBLIC WinMMOrdinal32
WinMMOrdinal32 PROC
    jmp DWORD PTR [_g_winmm_exports + 120]
WinMMOrdinal32 ENDP

PUBLIC WinMMOrdinal33
WinMMOrdinal33 PROC
    jmp DWORD PTR [_g_winmm_exports + 124]
WinMMOrdinal33 ENDP

PUBLIC WinMMOrdinal34
WinMMOrdinal34 PROC
    jmp DWORD PTR [_g_winmm_exports + 128]
WinMMOrdinal34 ENDP

PUBLIC WinMMOrdinal35
WinMMOrdinal35 PROC
    jmp DWORD PTR [_g_winmm_exports + 132]
WinMMOrdinal35 ENDP

PUBLIC WinMMOrdinal36
WinMMOrdinal36 PROC
    jmp DWORD PTR [_g_winmm_exports + 136]
WinMMOrdinal36 ENDP

PUBLIC WinMMOrdinal37
WinMMOrdinal37 PROC
    jmp DWORD PTR [_g_winmm_exports + 140]
WinMMOrdinal37 ENDP

PUBLIC WinMMOrdinal38
WinMMOrdinal38 PROC
    jmp DWORD PTR [_g_winmm_exports + 144]
WinMMOrdinal38 ENDP

PUBLIC WinMMOrdinal39
WinMMOrdinal39 PROC
    jmp DWORD PTR [_g_winmm_exports + 148]
WinMMOrdinal39 ENDP

PUBLIC WinMMOrdinal40
WinMMOrdinal40 PROC
    jmp DWORD PTR [_g_winmm_exports + 152]
WinMMOrdinal40 ENDP

PUBLIC WinMMOrdinal41
WinMMOrdinal41 PROC
    jmp DWORD PTR [_g_winmm_exports + 156]
WinMMOrdinal41 ENDP

PUBLIC WinMMOrdinal42
WinMMOrdinal42 PROC
    jmp DWORD PTR [_g_winmm_exports + 160]
WinMMOrdinal42 ENDP

PUBLIC WinMMOrdinal43
WinMMOrdinal43 PROC
    jmp DWORD PTR [_g_winmm_exports + 164]
WinMMOrdinal43 ENDP

PUBLIC WinMMOrdinal44
WinMMOrdinal44 PROC
    jmp DWORD PTR [_g_winmm_exports + 168]
WinMMOrdinal44 ENDP

PUBLIC WinMMOrdinal45
WinMMOrdinal45 PROC
    jmp DWORD PTR [_g_winmm_exports + 172]
WinMMOrdinal45 ENDP

PUBLIC WinMMOrdinal46
WinMMOrdinal46 PROC
    jmp DWORD PTR [_g_winmm_exports + 176]
WinMMOrdinal46 ENDP

PUBLIC WinMMOrdinal47
WinMMOrdinal47 PROC
    jmp DWORD PTR [_g_winmm_exports + 180]
WinMMOrdinal47 ENDP

PUBLIC WinMMOrdinal48
WinMMOrdinal48 PROC
    jmp DWORD PTR [_g_winmm_exports + 184]
WinMMOrdinal48 ENDP

PUBLIC WinMMOrdinal49
WinMMOrdinal49 PROC
    jmp DWORD PTR [_g_winmm_exports + 188]
WinMMOrdinal49 ENDP

PUBLIC WinMMOrdinal50
WinMMOrdinal50 PROC
    jmp DWORD PTR [_g_winmm_exports + 192]
WinMMOrdinal50 ENDP

PUBLIC WinMMOrdinal51
WinMMOrdinal51 PROC
    jmp DWORD PTR [_g_winmm_exports + 196]
WinMMOrdinal51 ENDP

PUBLIC WinMMOrdinal52
WinMMOrdinal52 PROC
    jmp DWORD PTR [_g_winmm_exports + 200]
WinMMOrdinal52 ENDP

PUBLIC WinMMOrdinal53
WinMMOrdinal53 PROC
    jmp DWORD PTR [_g_winmm_exports + 204]
WinMMOrdinal53 ENDP

PUBLIC WinMMOrdinal54
WinMMOrdinal54 PROC
    jmp DWORD PTR [_g_winmm_exports + 208]
WinMMOrdinal54 ENDP

PUBLIC WinMMOrdinal55
WinMMOrdinal55 PROC
    jmp DWORD PTR [_g_winmm_exports + 212]
WinMMOrdinal55 ENDP

PUBLIC WinMMOrdinal56
WinMMOrdinal56 PROC
    jmp DWORD PTR [_g_winmm_exports + 216]
WinMMOrdinal56 ENDP

PUBLIC WinMMOrdinal57
WinMMOrdinal57 PROC
    jmp DWORD PTR [_g_winmm_exports + 220]
WinMMOrdinal57 ENDP

PUBLIC WinMMOrdinal58
WinMMOrdinal58 PROC
    jmp DWORD PTR [_g_winmm_exports + 224]
WinMMOrdinal58 ENDP

PUBLIC WinMMOrdinal59
WinMMOrdinal59 PROC
    jmp DWORD PTR [_g_winmm_exports + 228]
WinMMOrdinal59 ENDP

PUBLIC WinMMOrdinal60
WinMMOrdinal60 PROC
    jmp DWORD PTR [_g_winmm_exports + 232]
WinMMOrdinal60 ENDP

PUBLIC WinMMOrdinal61
WinMMOrdinal61 PROC
    jmp DWORD PTR [_g_winmm_exports + 236]
WinMMOrdinal61 ENDP

PUBLIC WinMMOrdinal62
WinMMOrdinal62 PROC
    jmp DWORD PTR [_g_winmm_exports + 240]
WinMMOrdinal62 ENDP

PUBLIC WinMMOrdinal63
WinMMOrdinal63 PROC
    jmp DWORD PTR [_g_winmm_exports + 244]
WinMMOrdinal63 ENDP

PUBLIC WinMMOrdinal64
WinMMOrdinal64 PROC
    jmp DWORD PTR [_g_winmm_exports + 248]
WinMMOrdinal64 ENDP

PUBLIC WinMMOrdinal65
WinMMOrdinal65 PROC
    jmp DWORD PTR [_g_winmm_exports + 252]
WinMMOrdinal65 ENDP

PUBLIC WinMMOrdinal66
WinMMOrdinal66 PROC
    jmp DWORD PTR [_g_winmm_exports + 256]
WinMMOrdinal66 ENDP

PUBLIC WinMMOrdinal67
WinMMOrdinal67 PROC
    jmp DWORD PTR [_g_winmm_exports + 260]
WinMMOrdinal67 ENDP

PUBLIC WinMMOrdinal68
WinMMOrdinal68 PROC
    jmp DWORD PTR [_g_winmm_exports + 264]
WinMMOrdinal68 ENDP

PUBLIC WinMMOrdinal69
WinMMOrdinal69 PROC
    jmp DWORD PTR [_g_winmm_exports + 268]
WinMMOrdinal69 ENDP

PUBLIC WinMMOrdinal70
WinMMOrdinal70 PROC
    jmp DWORD PTR [_g_winmm_exports + 272]
WinMMOrdinal70 ENDP

PUBLIC WinMMOrdinal71
WinMMOrdinal71 PROC
    jmp DWORD PTR [_g_winmm_exports + 276]
WinMMOrdinal71 ENDP

PUBLIC WinMMOrdinal72
WinMMOrdinal72 PROC
    jmp DWORD PTR [_g_winmm_exports + 280]
WinMMOrdinal72 ENDP

PUBLIC WinMMOrdinal73
WinMMOrdinal73 PROC
    jmp DWORD PTR [_g_winmm_exports + 284]
WinMMOrdinal73 ENDP

PUBLIC WinMMOrdinal74
WinMMOrdinal74 PROC
    jmp DWORD PTR [_g_winmm_exports + 288]
WinMMOrdinal74 ENDP

PUBLIC WinMMOrdinal75
WinMMOrdinal75 PROC
    jmp DWORD PTR [_g_winmm_exports + 292]
WinMMOrdinal75 ENDP

PUBLIC WinMMOrdinal76
WinMMOrdinal76 PROC
    jmp DWORD PTR [_g_winmm_exports + 296]
WinMMOrdinal76 ENDP

PUBLIC WinMMOrdinal77
WinMMOrdinal77 PROC
    jmp DWORD PTR [_g_winmm_exports + 300]
WinMMOrdinal77 ENDP

PUBLIC WinMMOrdinal78
WinMMOrdinal78 PROC
    jmp DWORD PTR [_g_winmm_exports + 304]
WinMMOrdinal78 ENDP

PUBLIC WinMMOrdinal79
WinMMOrdinal79 PROC
    jmp DWORD PTR [_g_winmm_exports + 308]
WinMMOrdinal79 ENDP

PUBLIC WinMMOrdinal80
WinMMOrdinal80 PROC
    jmp DWORD PTR [_g_winmm_exports + 312]
WinMMOrdinal80 ENDP

PUBLIC WinMMOrdinal81
WinMMOrdinal81 PROC
    jmp DWORD PTR [_g_winmm_exports + 316]
WinMMOrdinal81 ENDP

PUBLIC WinMMOrdinal82
WinMMOrdinal82 PROC
    jmp DWORD PTR [_g_winmm_exports + 320]
WinMMOrdinal82 ENDP

PUBLIC WinMMOrdinal83
WinMMOrdinal83 PROC
    jmp DWORD PTR [_g_winmm_exports + 324]
WinMMOrdinal83 ENDP

PUBLIC WinMMOrdinal84
WinMMOrdinal84 PROC
    jmp DWORD PTR [_g_winmm_exports + 328]
WinMMOrdinal84 ENDP

PUBLIC WinMMOrdinal85
WinMMOrdinal85 PROC
    jmp DWORD PTR [_g_winmm_exports + 332]
WinMMOrdinal85 ENDP

PUBLIC WinMMOrdinal86
WinMMOrdinal86 PROC
    jmp DWORD PTR [_g_winmm_exports + 336]
WinMMOrdinal86 ENDP

PUBLIC WinMMOrdinal87
WinMMOrdinal87 PROC
    jmp DWORD PTR [_g_winmm_exports + 340]
WinMMOrdinal87 ENDP

PUBLIC WinMMOrdinal88
WinMMOrdinal88 PROC
    jmp DWORD PTR [_g_winmm_exports + 344]
WinMMOrdinal88 ENDP

PUBLIC WinMMOrdinal89
WinMMOrdinal89 PROC
    jmp DWORD PTR [_g_winmm_exports + 348]
WinMMOrdinal89 ENDP

PUBLIC WinMMOrdinal90
WinMMOrdinal90 PROC
    jmp DWORD PTR [_g_winmm_exports + 352]
WinMMOrdinal90 ENDP

PUBLIC WinMMOrdinal91
WinMMOrdinal91 PROC
    jmp DWORD PTR [_g_winmm_exports + 356]
WinMMOrdinal91 ENDP

PUBLIC WinMMOrdinal92
WinMMOrdinal92 PROC
    jmp DWORD PTR [_g_winmm_exports + 360]
WinMMOrdinal92 ENDP

PUBLIC WinMMOrdinal93
WinMMOrdinal93 PROC
    jmp DWORD PTR [_g_winmm_exports + 364]
WinMMOrdinal93 ENDP

PUBLIC WinMMOrdinal94
WinMMOrdinal94 PROC
    jmp DWORD PTR [_g_winmm_exports + 368]
WinMMOrdinal94 ENDP

PUBLIC WinMMOrdinal95
WinMMOrdinal95 PROC
    jmp DWORD PTR [_g_winmm_exports + 372]
WinMMOrdinal95 ENDP

PUBLIC WinMMOrdinal96
WinMMOrdinal96 PROC
    jmp DWORD PTR [_g_winmm_exports + 376]
WinMMOrdinal96 ENDP

PUBLIC WinMMOrdinal97
WinMMOrdinal97 PROC
    jmp DWORD PTR [_g_winmm_exports + 380]
WinMMOrdinal97 ENDP

PUBLIC WinMMOrdinal98
WinMMOrdinal98 PROC
    jmp DWORD PTR [_g_winmm_exports + 384]
WinMMOrdinal98 ENDP

PUBLIC WinMMOrdinal99
WinMMOrdinal99 PROC
    jmp DWORD PTR [_g_winmm_exports + 388]
WinMMOrdinal99 ENDP

PUBLIC WinMMOrdinal100
WinMMOrdinal100 PROC
    jmp DWORD PTR [_g_winmm_exports + 392]
WinMMOrdinal100 ENDP

PUBLIC WinMMOrdinal101
WinMMOrdinal101 PROC
    jmp DWORD PTR [_g_winmm_exports + 396]
WinMMOrdinal101 ENDP

PUBLIC WinMMOrdinal102
WinMMOrdinal102 PROC
    jmp DWORD PTR [_g_winmm_exports + 400]
WinMMOrdinal102 ENDP

PUBLIC WinMMOrdinal103
WinMMOrdinal103 PROC
    jmp DWORD PTR [_g_winmm_exports + 404]
WinMMOrdinal103 ENDP

PUBLIC WinMMOrdinal104
WinMMOrdinal104 PROC
    jmp DWORD PTR [_g_winmm_exports + 408]
WinMMOrdinal104 ENDP

PUBLIC WinMMOrdinal105
WinMMOrdinal105 PROC
    jmp DWORD PTR [_g_winmm_exports + 412]
WinMMOrdinal105 ENDP

PUBLIC WinMMOrdinal106
WinMMOrdinal106 PROC
    jmp DWORD PTR [_g_winmm_exports + 416]
WinMMOrdinal106 ENDP

PUBLIC WinMMOrdinal107
WinMMOrdinal107 PROC
    jmp DWORD PTR [_g_winmm_exports + 420]
WinMMOrdinal107 ENDP

PUBLIC WinMMOrdinal108
WinMMOrdinal108 PROC
    jmp DWORD PTR [_g_winmm_exports + 424]
WinMMOrdinal108 ENDP

PUBLIC WinMMOrdinal109
WinMMOrdinal109 PROC
    jmp DWORD PTR [_g_winmm_exports + 428]
WinMMOrdinal109 ENDP

PUBLIC WinMMOrdinal110
WinMMOrdinal110 PROC
    jmp DWORD PTR [_g_winmm_exports + 432]
WinMMOrdinal110 ENDP

PUBLIC WinMMOrdinal111
WinMMOrdinal111 PROC
    jmp DWORD PTR [_g_winmm_exports + 436]
WinMMOrdinal111 ENDP

PUBLIC WinMMOrdinal112
WinMMOrdinal112 PROC
    jmp DWORD PTR [_g_winmm_exports + 440]
WinMMOrdinal112 ENDP

PUBLIC WinMMOrdinal113
WinMMOrdinal113 PROC
    jmp DWORD PTR [_g_winmm_exports + 444]
WinMMOrdinal113 ENDP

PUBLIC WinMMOrdinal114
WinMMOrdinal114 PROC
    jmp DWORD PTR [_g_winmm_exports + 448]
WinMMOrdinal114 ENDP

PUBLIC WinMMOrdinal115
WinMMOrdinal115 PROC
    jmp DWORD PTR [_g_winmm_exports + 452]
WinMMOrdinal115 ENDP

PUBLIC WinMMOrdinal116
WinMMOrdinal116 PROC
    jmp DWORD PTR [_g_winmm_exports + 456]
WinMMOrdinal116 ENDP

PUBLIC WinMMOrdinal117
WinMMOrdinal117 PROC
    jmp DWORD PTR [_g_winmm_exports + 460]
WinMMOrdinal117 ENDP

PUBLIC WinMMOrdinal118
WinMMOrdinal118 PROC
    jmp DWORD PTR [_g_winmm_exports + 464]
WinMMOrdinal118 ENDP

PUBLIC WinMMOrdinal119
WinMMOrdinal119 PROC
    jmp DWORD PTR [_g_winmm_exports + 468]
WinMMOrdinal119 ENDP

PUBLIC WinMMOrdinal120
WinMMOrdinal120 PROC
    jmp DWORD PTR [_g_winmm_exports + 472]
WinMMOrdinal120 ENDP

PUBLIC WinMMOrdinal121
WinMMOrdinal121 PROC
    jmp DWORD PTR [_g_winmm_exports + 476]
WinMMOrdinal121 ENDP

PUBLIC WinMMOrdinal122
WinMMOrdinal122 PROC
    jmp DWORD PTR [_g_winmm_exports + 480]
WinMMOrdinal122 ENDP

PUBLIC WinMMOrdinal123
WinMMOrdinal123 PROC
    jmp DWORD PTR [_g_winmm_exports + 484]
WinMMOrdinal123 ENDP

PUBLIC WinMMOrdinal124
WinMMOrdinal124 PROC
    jmp DWORD PTR [_g_winmm_exports + 488]
WinMMOrdinal124 ENDP

PUBLIC WinMMOrdinal125
WinMMOrdinal125 PROC
    jmp DWORD PTR [_g_winmm_exports + 492]
WinMMOrdinal125 ENDP

PUBLIC WinMMOrdinal126
WinMMOrdinal126 PROC
    jmp DWORD PTR [_g_winmm_exports + 496]
WinMMOrdinal126 ENDP

PUBLIC WinMMOrdinal127
WinMMOrdinal127 PROC
    jmp DWORD PTR [_g_winmm_exports + 500]
WinMMOrdinal127 ENDP

PUBLIC WinMMOrdinal128
WinMMOrdinal128 PROC
    jmp DWORD PTR [_g_winmm_exports + 504]
WinMMOrdinal128 ENDP

PUBLIC WinMMOrdinal129
WinMMOrdinal129 PROC
    jmp DWORD PTR [_g_winmm_exports + 508]
WinMMOrdinal129 ENDP

PUBLIC WinMMOrdinal130
WinMMOrdinal130 PROC
    jmp DWORD PTR [_g_winmm_exports + 512]
WinMMOrdinal130 ENDP

PUBLIC WinMMOrdinal131
WinMMOrdinal131 PROC
    jmp DWORD PTR [_g_winmm_exports + 516]
WinMMOrdinal131 ENDP

PUBLIC WinMMOrdinal132
WinMMOrdinal132 PROC
    jmp DWORD PTR [_g_winmm_exports + 520]
WinMMOrdinal132 ENDP

PUBLIC WinMMOrdinal133
WinMMOrdinal133 PROC
    jmp DWORD PTR [_g_winmm_exports + 524]
WinMMOrdinal133 ENDP

PUBLIC WinMMOrdinal134
WinMMOrdinal134 PROC
    jmp DWORD PTR [_g_winmm_exports + 528]
WinMMOrdinal134 ENDP

PUBLIC WinMMOrdinal135
WinMMOrdinal135 PROC
    jmp DWORD PTR [_g_winmm_exports + 532]
WinMMOrdinal135 ENDP

PUBLIC WinMMOrdinal136
WinMMOrdinal136 PROC
    jmp DWORD PTR [_g_winmm_exports + 536]
WinMMOrdinal136 ENDP

PUBLIC WinMMOrdinal137
WinMMOrdinal137 PROC
    jmp DWORD PTR [_g_winmm_exports + 540]
WinMMOrdinal137 ENDP

PUBLIC WinMMOrdinal138
WinMMOrdinal138 PROC
    jmp DWORD PTR [_g_winmm_exports + 544]
WinMMOrdinal138 ENDP

PUBLIC WinMMOrdinal139
WinMMOrdinal139 PROC
    jmp DWORD PTR [_g_winmm_exports + 548]
WinMMOrdinal139 ENDP

PUBLIC WinMMOrdinal140
WinMMOrdinal140 PROC
    jmp DWORD PTR [_g_winmm_exports + 552]
WinMMOrdinal140 ENDP

PUBLIC WinMMOrdinal141
WinMMOrdinal141 PROC
    jmp DWORD PTR [_g_winmm_exports + 556]
WinMMOrdinal141 ENDP

PUBLIC WinMMOrdinal142
WinMMOrdinal142 PROC
    jmp DWORD PTR [_g_winmm_exports + 560]
WinMMOrdinal142 ENDP

PUBLIC WinMMOrdinal143
WinMMOrdinal143 PROC
    jmp DWORD PTR [_g_winmm_exports + 564]
WinMMOrdinal143 ENDP

PUBLIC WinMMOrdinal144
WinMMOrdinal144 PROC
    jmp DWORD PTR [_g_winmm_exports + 568]
WinMMOrdinal144 ENDP

PUBLIC WinMMOrdinal145
WinMMOrdinal145 PROC
    jmp DWORD PTR [_g_winmm_exports + 572]
WinMMOrdinal145 ENDP

PUBLIC WinMMOrdinal146
WinMMOrdinal146 PROC
    jmp DWORD PTR [_g_winmm_exports + 576]
WinMMOrdinal146 ENDP

PUBLIC WinMMOrdinal147
WinMMOrdinal147 PROC
    jmp DWORD PTR [_g_winmm_exports + 580]
WinMMOrdinal147 ENDP

PUBLIC WinMMOrdinal148
WinMMOrdinal148 PROC
    jmp DWORD PTR [_g_winmm_exports + 584]
WinMMOrdinal148 ENDP

PUBLIC WinMMOrdinal149
WinMMOrdinal149 PROC
    jmp DWORD PTR [_g_winmm_exports + 588]
WinMMOrdinal149 ENDP

PUBLIC WinMMOrdinal150
WinMMOrdinal150 PROC
    jmp DWORD PTR [_g_winmm_exports + 592]
WinMMOrdinal150 ENDP

PUBLIC WinMMOrdinal151
WinMMOrdinal151 PROC
    jmp DWORD PTR [_g_winmm_exports + 596]
WinMMOrdinal151 ENDP

PUBLIC WinMMOrdinal152
WinMMOrdinal152 PROC
    jmp DWORD PTR [_g_winmm_exports + 600]
WinMMOrdinal152 ENDP

PUBLIC WinMMOrdinal153
WinMMOrdinal153 PROC
    jmp DWORD PTR [_g_winmm_exports + 604]
WinMMOrdinal153 ENDP

PUBLIC WinMMOrdinal154
WinMMOrdinal154 PROC
    jmp DWORD PTR [_g_winmm_exports + 608]
WinMMOrdinal154 ENDP

PUBLIC WinMMOrdinal155
WinMMOrdinal155 PROC
    jmp DWORD PTR [_g_winmm_exports + 612]
WinMMOrdinal155 ENDP

PUBLIC WinMMOrdinal156
WinMMOrdinal156 PROC
    jmp DWORD PTR [_g_winmm_exports + 616]
WinMMOrdinal156 ENDP

PUBLIC WinMMOrdinal157
WinMMOrdinal157 PROC
    jmp DWORD PTR [_g_winmm_exports + 620]
WinMMOrdinal157 ENDP

PUBLIC WinMMOrdinal158
WinMMOrdinal158 PROC
    jmp DWORD PTR [_g_winmm_exports + 624]
WinMMOrdinal158 ENDP

PUBLIC WinMMOrdinal159
WinMMOrdinal159 PROC
    jmp DWORD PTR [_g_winmm_exports + 628]
WinMMOrdinal159 ENDP

PUBLIC WinMMOrdinal160
WinMMOrdinal160 PROC
    jmp DWORD PTR [_g_winmm_exports + 632]
WinMMOrdinal160 ENDP

PUBLIC WinMMOrdinal161
WinMMOrdinal161 PROC
    jmp DWORD PTR [_g_winmm_exports + 636]
WinMMOrdinal161 ENDP

PUBLIC WinMMOrdinal162
WinMMOrdinal162 PROC
    jmp DWORD PTR [_g_winmm_exports + 640]
WinMMOrdinal162 ENDP

PUBLIC WinMMOrdinal163
WinMMOrdinal163 PROC
    jmp DWORD PTR [_g_winmm_exports + 644]
WinMMOrdinal163 ENDP

PUBLIC WinMMOrdinal164
WinMMOrdinal164 PROC
    jmp DWORD PTR [_g_winmm_exports + 648]
WinMMOrdinal164 ENDP

PUBLIC WinMMOrdinal165
WinMMOrdinal165 PROC
    jmp DWORD PTR [_g_winmm_exports + 652]
WinMMOrdinal165 ENDP

PUBLIC WinMMOrdinal166
WinMMOrdinal166 PROC
    jmp DWORD PTR [_g_winmm_exports + 656]
WinMMOrdinal166 ENDP

PUBLIC WinMMOrdinal167
WinMMOrdinal167 PROC
    jmp DWORD PTR [_g_winmm_exports + 660]
WinMMOrdinal167 ENDP

PUBLIC WinMMOrdinal168
WinMMOrdinal168 PROC
    jmp DWORD PTR [_g_winmm_exports + 664]
WinMMOrdinal168 ENDP

PUBLIC WinMMOrdinal169
WinMMOrdinal169 PROC
    jmp DWORD PTR [_g_winmm_exports + 668]
WinMMOrdinal169 ENDP

PUBLIC WinMMOrdinal170
WinMMOrdinal170 PROC
    jmp DWORD PTR [_g_winmm_exports + 672]
WinMMOrdinal170 ENDP

PUBLIC WinMMOrdinal171
WinMMOrdinal171 PROC
    jmp DWORD PTR [_g_winmm_exports + 676]
WinMMOrdinal171 ENDP

PUBLIC WinMMOrdinal172
WinMMOrdinal172 PROC
    jmp DWORD PTR [_g_winmm_exports + 680]
WinMMOrdinal172 ENDP

PUBLIC WinMMOrdinal173
WinMMOrdinal173 PROC
    jmp DWORD PTR [_g_winmm_exports + 684]
WinMMOrdinal173 ENDP

PUBLIC WinMMOrdinal174
WinMMOrdinal174 PROC
    jmp DWORD PTR [_g_winmm_exports + 688]
WinMMOrdinal174 ENDP

PUBLIC WinMMOrdinal175
WinMMOrdinal175 PROC
    jmp DWORD PTR [_g_winmm_exports + 692]
WinMMOrdinal175 ENDP

PUBLIC WinMMOrdinal176
WinMMOrdinal176 PROC
    jmp DWORD PTR [_g_winmm_exports + 696]
WinMMOrdinal176 ENDP

PUBLIC WinMMOrdinal177
WinMMOrdinal177 PROC
    jmp DWORD PTR [_g_winmm_exports + 700]
WinMMOrdinal177 ENDP

PUBLIC WinMMOrdinal178
WinMMOrdinal178 PROC
    jmp DWORD PTR [_g_winmm_exports + 704]
WinMMOrdinal178 ENDP

PUBLIC WinMMOrdinal179
WinMMOrdinal179 PROC
    jmp DWORD PTR [_g_winmm_exports + 708]
WinMMOrdinal179 ENDP

PUBLIC WinMMOrdinal180
WinMMOrdinal180 PROC
    jmp DWORD PTR [_g_winmm_exports + 712]
WinMMOrdinal180 ENDP

PUBLIC WinMMOrdinal181
WinMMOrdinal181 PROC
    jmp DWORD PTR [_g_winmm_exports + 716]
WinMMOrdinal181 ENDP

PUBLIC WinMMOrdinal182
WinMMOrdinal182 PROC
    jmp DWORD PTR [_g_winmm_exports + 720]
WinMMOrdinal182 ENDP

PUBLIC WinMMOrdinal183
WinMMOrdinal183 PROC
    jmp DWORD PTR [_g_winmm_exports + 724]
WinMMOrdinal183 ENDP

PUBLIC WinMMOrdinal184
WinMMOrdinal184 PROC
    jmp DWORD PTR [_g_winmm_exports + 728]
WinMMOrdinal184 ENDP

PUBLIC WinMMOrdinal185
WinMMOrdinal185 PROC
    jmp DWORD PTR [_g_winmm_exports + 732]
WinMMOrdinal185 ENDP

PUBLIC WinMMOrdinal186
WinMMOrdinal186 PROC
    jmp DWORD PTR [_g_winmm_exports + 736]
WinMMOrdinal186 ENDP

PUBLIC WinMMOrdinal187
WinMMOrdinal187 PROC
    jmp DWORD PTR [_g_winmm_exports + 740]
WinMMOrdinal187 ENDP

PUBLIC WinMMOrdinal188
WinMMOrdinal188 PROC
    jmp DWORD PTR [_g_winmm_exports + 744]
WinMMOrdinal188 ENDP

PUBLIC WinMMOrdinal189
WinMMOrdinal189 PROC
    jmp DWORD PTR [_g_winmm_exports + 748]
WinMMOrdinal189 ENDP

PUBLIC WinMMOrdinal190
WinMMOrdinal190 PROC
    jmp DWORD PTR [_g_winmm_exports + 752]
WinMMOrdinal190 ENDP

PUBLIC WinMMOrdinal191
WinMMOrdinal191 PROC
    jmp DWORD PTR [_g_winmm_exports + 756]
WinMMOrdinal191 ENDP

PUBLIC WinMMOrdinal192
WinMMOrdinal192 PROC
    jmp DWORD PTR [_g_winmm_exports + 760]
WinMMOrdinal192 ENDP

PUBLIC WinMMOrdinal193
WinMMOrdinal193 PROC
    jmp DWORD PTR [_g_winmm_exports + 764]
WinMMOrdinal193 ENDP

PUBLIC WinMMOrdinal194
WinMMOrdinal194 PROC
    jmp DWORD PTR [_g_winmm_exports + 768]
WinMMOrdinal194 ENDP

END
