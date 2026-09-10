.386
.model flat
EXTERN _g_winmm_functions:DWORD
EXTERN _ResolveWinmmExport:PROC
.code
_winmm_stub_0 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 0]
    test eax, eax
    jne winmm_ready_0
    push 0
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_0:
    jmp eax
_winmm_stub_0 ENDP
_winmm_stub_1 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 4]
    test eax, eax
    jne winmm_ready_1
    push 1
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_1:
    jmp eax
_winmm_stub_1 ENDP
_winmm_stub_2 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 8]
    test eax, eax
    jne winmm_ready_2
    push 2
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_2:
    jmp eax
_winmm_stub_2 ENDP
_winmm_stub_3 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 12]
    test eax, eax
    jne winmm_ready_3
    push 3
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_3:
    jmp eax
_winmm_stub_3 ENDP
_winmm_stub_4 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 16]
    test eax, eax
    jne winmm_ready_4
    push 4
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_4:
    jmp eax
_winmm_stub_4 ENDP
_winmm_stub_5 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 20]
    test eax, eax
    jne winmm_ready_5
    push 5
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_5:
    jmp eax
_winmm_stub_5 ENDP
_winmm_stub_6 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 24]
    test eax, eax
    jne winmm_ready_6
    push 6
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_6:
    jmp eax
_winmm_stub_6 ENDP
_winmm_stub_7 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 28]
    test eax, eax
    jne winmm_ready_7
    push 7
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_7:
    jmp eax
_winmm_stub_7 ENDP
_winmm_stub_8 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 32]
    test eax, eax
    jne winmm_ready_8
    push 8
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_8:
    jmp eax
_winmm_stub_8 ENDP
_winmm_stub_9 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 36]
    test eax, eax
    jne winmm_ready_9
    push 9
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_9:
    jmp eax
_winmm_stub_9 ENDP
_winmm_stub_10 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 40]
    test eax, eax
    jne winmm_ready_10
    push 10
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_10:
    jmp eax
_winmm_stub_10 ENDP
_winmm_stub_11 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 44]
    test eax, eax
    jne winmm_ready_11
    push 11
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_11:
    jmp eax
_winmm_stub_11 ENDP
_winmm_stub_12 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 48]
    test eax, eax
    jne winmm_ready_12
    push 12
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_12:
    jmp eax
_winmm_stub_12 ENDP
_winmm_stub_13 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 52]
    test eax, eax
    jne winmm_ready_13
    push 13
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_13:
    jmp eax
_winmm_stub_13 ENDP
_winmm_stub_14 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 56]
    test eax, eax
    jne winmm_ready_14
    push 14
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_14:
    jmp eax
_winmm_stub_14 ENDP
_winmm_stub_15 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 60]
    test eax, eax
    jne winmm_ready_15
    push 15
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_15:
    jmp eax
_winmm_stub_15 ENDP
_winmm_stub_16 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 64]
    test eax, eax
    jne winmm_ready_16
    push 16
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_16:
    jmp eax
_winmm_stub_16 ENDP
_winmm_stub_17 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 68]
    test eax, eax
    jne winmm_ready_17
    push 17
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_17:
    jmp eax
_winmm_stub_17 ENDP
_winmm_stub_18 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 72]
    test eax, eax
    jne winmm_ready_18
    push 18
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_18:
    jmp eax
_winmm_stub_18 ENDP
_winmm_stub_19 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 76]
    test eax, eax
    jne winmm_ready_19
    push 19
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_19:
    jmp eax
_winmm_stub_19 ENDP
_winmm_stub_20 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 80]
    test eax, eax
    jne winmm_ready_20
    push 20
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_20:
    jmp eax
_winmm_stub_20 ENDP
_winmm_stub_21 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 84]
    test eax, eax
    jne winmm_ready_21
    push 21
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_21:
    jmp eax
_winmm_stub_21 ENDP
_winmm_stub_22 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 88]
    test eax, eax
    jne winmm_ready_22
    push 22
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_22:
    jmp eax
_winmm_stub_22 ENDP
_winmm_stub_23 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 92]
    test eax, eax
    jne winmm_ready_23
    push 23
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_23:
    jmp eax
_winmm_stub_23 ENDP
_winmm_stub_24 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 96]
    test eax, eax
    jne winmm_ready_24
    push 24
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_24:
    jmp eax
_winmm_stub_24 ENDP
_winmm_stub_25 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 100]
    test eax, eax
    jne winmm_ready_25
    push 25
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_25:
    jmp eax
_winmm_stub_25 ENDP
_winmm_stub_26 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 104]
    test eax, eax
    jne winmm_ready_26
    push 26
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_26:
    jmp eax
_winmm_stub_26 ENDP
_winmm_stub_27 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 108]
    test eax, eax
    jne winmm_ready_27
    push 27
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_27:
    jmp eax
_winmm_stub_27 ENDP
_winmm_stub_28 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 112]
    test eax, eax
    jne winmm_ready_28
    push 28
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_28:
    jmp eax
_winmm_stub_28 ENDP
_winmm_stub_29 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 116]
    test eax, eax
    jne winmm_ready_29
    push 29
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_29:
    jmp eax
_winmm_stub_29 ENDP
_winmm_stub_30 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 120]
    test eax, eax
    jne winmm_ready_30
    push 30
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_30:
    jmp eax
_winmm_stub_30 ENDP
_winmm_stub_31 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 124]
    test eax, eax
    jne winmm_ready_31
    push 31
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_31:
    jmp eax
_winmm_stub_31 ENDP
_winmm_stub_32 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 128]
    test eax, eax
    jne winmm_ready_32
    push 32
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_32:
    jmp eax
_winmm_stub_32 ENDP
_winmm_stub_33 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 132]
    test eax, eax
    jne winmm_ready_33
    push 33
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_33:
    jmp eax
_winmm_stub_33 ENDP
_winmm_stub_34 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 136]
    test eax, eax
    jne winmm_ready_34
    push 34
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_34:
    jmp eax
_winmm_stub_34 ENDP
_winmm_stub_35 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 140]
    test eax, eax
    jne winmm_ready_35
    push 35
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_35:
    jmp eax
_winmm_stub_35 ENDP
_winmm_stub_36 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 144]
    test eax, eax
    jne winmm_ready_36
    push 36
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_36:
    jmp eax
_winmm_stub_36 ENDP
_winmm_stub_37 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 148]
    test eax, eax
    jne winmm_ready_37
    push 37
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_37:
    jmp eax
_winmm_stub_37 ENDP
_winmm_stub_38 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 152]
    test eax, eax
    jne winmm_ready_38
    push 38
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_38:
    jmp eax
_winmm_stub_38 ENDP
_winmm_stub_39 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 156]
    test eax, eax
    jne winmm_ready_39
    push 39
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_39:
    jmp eax
_winmm_stub_39 ENDP
_winmm_stub_40 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 160]
    test eax, eax
    jne winmm_ready_40
    push 40
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_40:
    jmp eax
_winmm_stub_40 ENDP
_winmm_stub_41 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 164]
    test eax, eax
    jne winmm_ready_41
    push 41
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_41:
    jmp eax
_winmm_stub_41 ENDP
_winmm_stub_42 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 168]
    test eax, eax
    jne winmm_ready_42
    push 42
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_42:
    jmp eax
_winmm_stub_42 ENDP
_winmm_stub_43 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 172]
    test eax, eax
    jne winmm_ready_43
    push 43
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_43:
    jmp eax
_winmm_stub_43 ENDP
_winmm_stub_44 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 176]
    test eax, eax
    jne winmm_ready_44
    push 44
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_44:
    jmp eax
_winmm_stub_44 ENDP
_winmm_stub_45 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 180]
    test eax, eax
    jne winmm_ready_45
    push 45
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_45:
    jmp eax
_winmm_stub_45 ENDP
_winmm_stub_46 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 184]
    test eax, eax
    jne winmm_ready_46
    push 46
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_46:
    jmp eax
_winmm_stub_46 ENDP
_winmm_stub_47 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 188]
    test eax, eax
    jne winmm_ready_47
    push 47
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_47:
    jmp eax
_winmm_stub_47 ENDP
_winmm_stub_48 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 192]
    test eax, eax
    jne winmm_ready_48
    push 48
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_48:
    jmp eax
_winmm_stub_48 ENDP
_winmm_stub_49 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 196]
    test eax, eax
    jne winmm_ready_49
    push 49
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_49:
    jmp eax
_winmm_stub_49 ENDP
_winmm_stub_50 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 200]
    test eax, eax
    jne winmm_ready_50
    push 50
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_50:
    jmp eax
_winmm_stub_50 ENDP
_winmm_stub_51 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 204]
    test eax, eax
    jne winmm_ready_51
    push 51
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_51:
    jmp eax
_winmm_stub_51 ENDP
_winmm_stub_52 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 208]
    test eax, eax
    jne winmm_ready_52
    push 52
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_52:
    jmp eax
_winmm_stub_52 ENDP
_winmm_stub_53 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 212]
    test eax, eax
    jne winmm_ready_53
    push 53
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_53:
    jmp eax
_winmm_stub_53 ENDP
_winmm_stub_54 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 216]
    test eax, eax
    jne winmm_ready_54
    push 54
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_54:
    jmp eax
_winmm_stub_54 ENDP
_winmm_stub_55 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 220]
    test eax, eax
    jne winmm_ready_55
    push 55
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_55:
    jmp eax
_winmm_stub_55 ENDP
_winmm_stub_56 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 224]
    test eax, eax
    jne winmm_ready_56
    push 56
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_56:
    jmp eax
_winmm_stub_56 ENDP
_winmm_stub_57 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 228]
    test eax, eax
    jne winmm_ready_57
    push 57
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_57:
    jmp eax
_winmm_stub_57 ENDP
_winmm_stub_58 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 232]
    test eax, eax
    jne winmm_ready_58
    push 58
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_58:
    jmp eax
_winmm_stub_58 ENDP
_winmm_stub_59 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 236]
    test eax, eax
    jne winmm_ready_59
    push 59
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_59:
    jmp eax
_winmm_stub_59 ENDP
_winmm_stub_60 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 240]
    test eax, eax
    jne winmm_ready_60
    push 60
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_60:
    jmp eax
_winmm_stub_60 ENDP
_winmm_stub_61 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 244]
    test eax, eax
    jne winmm_ready_61
    push 61
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_61:
    jmp eax
_winmm_stub_61 ENDP
_winmm_stub_62 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 248]
    test eax, eax
    jne winmm_ready_62
    push 62
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_62:
    jmp eax
_winmm_stub_62 ENDP
_winmm_stub_63 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 252]
    test eax, eax
    jne winmm_ready_63
    push 63
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_63:
    jmp eax
_winmm_stub_63 ENDP
_winmm_stub_64 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 256]
    test eax, eax
    jne winmm_ready_64
    push 64
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_64:
    jmp eax
_winmm_stub_64 ENDP
_winmm_stub_65 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 260]
    test eax, eax
    jne winmm_ready_65
    push 65
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_65:
    jmp eax
_winmm_stub_65 ENDP
_winmm_stub_66 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 264]
    test eax, eax
    jne winmm_ready_66
    push 66
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_66:
    jmp eax
_winmm_stub_66 ENDP
_winmm_stub_67 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 268]
    test eax, eax
    jne winmm_ready_67
    push 67
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_67:
    jmp eax
_winmm_stub_67 ENDP
_winmm_stub_68 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 272]
    test eax, eax
    jne winmm_ready_68
    push 68
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_68:
    jmp eax
_winmm_stub_68 ENDP
_winmm_stub_69 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 276]
    test eax, eax
    jne winmm_ready_69
    push 69
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_69:
    jmp eax
_winmm_stub_69 ENDP
_winmm_stub_70 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 280]
    test eax, eax
    jne winmm_ready_70
    push 70
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_70:
    jmp eax
_winmm_stub_70 ENDP
_winmm_stub_71 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 284]
    test eax, eax
    jne winmm_ready_71
    push 71
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_71:
    jmp eax
_winmm_stub_71 ENDP
_winmm_stub_72 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 288]
    test eax, eax
    jne winmm_ready_72
    push 72
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_72:
    jmp eax
_winmm_stub_72 ENDP
_winmm_stub_73 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 292]
    test eax, eax
    jne winmm_ready_73
    push 73
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_73:
    jmp eax
_winmm_stub_73 ENDP
_winmm_stub_74 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 296]
    test eax, eax
    jne winmm_ready_74
    push 74
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_74:
    jmp eax
_winmm_stub_74 ENDP
_winmm_stub_75 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 300]
    test eax, eax
    jne winmm_ready_75
    push 75
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_75:
    jmp eax
_winmm_stub_75 ENDP
_winmm_stub_76 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 304]
    test eax, eax
    jne winmm_ready_76
    push 76
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_76:
    jmp eax
_winmm_stub_76 ENDP
_winmm_stub_77 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 308]
    test eax, eax
    jne winmm_ready_77
    push 77
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_77:
    jmp eax
_winmm_stub_77 ENDP
_winmm_stub_78 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 312]
    test eax, eax
    jne winmm_ready_78
    push 78
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_78:
    jmp eax
_winmm_stub_78 ENDP
_winmm_stub_79 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 316]
    test eax, eax
    jne winmm_ready_79
    push 79
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_79:
    jmp eax
_winmm_stub_79 ENDP
_winmm_stub_80 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 320]
    test eax, eax
    jne winmm_ready_80
    push 80
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_80:
    jmp eax
_winmm_stub_80 ENDP
_winmm_stub_81 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 324]
    test eax, eax
    jne winmm_ready_81
    push 81
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_81:
    jmp eax
_winmm_stub_81 ENDP
_winmm_stub_82 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 328]
    test eax, eax
    jne winmm_ready_82
    push 82
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_82:
    jmp eax
_winmm_stub_82 ENDP
_winmm_stub_83 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 332]
    test eax, eax
    jne winmm_ready_83
    push 83
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_83:
    jmp eax
_winmm_stub_83 ENDP
_winmm_stub_84 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 336]
    test eax, eax
    jne winmm_ready_84
    push 84
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_84:
    jmp eax
_winmm_stub_84 ENDP
_winmm_stub_85 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 340]
    test eax, eax
    jne winmm_ready_85
    push 85
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_85:
    jmp eax
_winmm_stub_85 ENDP
_winmm_stub_86 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 344]
    test eax, eax
    jne winmm_ready_86
    push 86
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_86:
    jmp eax
_winmm_stub_86 ENDP
_winmm_stub_87 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 348]
    test eax, eax
    jne winmm_ready_87
    push 87
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_87:
    jmp eax
_winmm_stub_87 ENDP
_winmm_stub_88 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 352]
    test eax, eax
    jne winmm_ready_88
    push 88
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_88:
    jmp eax
_winmm_stub_88 ENDP
_winmm_stub_89 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 356]
    test eax, eax
    jne winmm_ready_89
    push 89
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_89:
    jmp eax
_winmm_stub_89 ENDP
_winmm_stub_90 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 360]
    test eax, eax
    jne winmm_ready_90
    push 90
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_90:
    jmp eax
_winmm_stub_90 ENDP
_winmm_stub_91 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 364]
    test eax, eax
    jne winmm_ready_91
    push 91
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_91:
    jmp eax
_winmm_stub_91 ENDP
_winmm_stub_92 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 368]
    test eax, eax
    jne winmm_ready_92
    push 92
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_92:
    jmp eax
_winmm_stub_92 ENDP
_winmm_stub_93 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 372]
    test eax, eax
    jne winmm_ready_93
    push 93
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_93:
    jmp eax
_winmm_stub_93 ENDP
_winmm_stub_94 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 376]
    test eax, eax
    jne winmm_ready_94
    push 94
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_94:
    jmp eax
_winmm_stub_94 ENDP
_winmm_stub_95 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 380]
    test eax, eax
    jne winmm_ready_95
    push 95
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_95:
    jmp eax
_winmm_stub_95 ENDP
_winmm_stub_96 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 384]
    test eax, eax
    jne winmm_ready_96
    push 96
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_96:
    jmp eax
_winmm_stub_96 ENDP
_winmm_stub_97 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 388]
    test eax, eax
    jne winmm_ready_97
    push 97
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_97:
    jmp eax
_winmm_stub_97 ENDP
_winmm_stub_98 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 392]
    test eax, eax
    jne winmm_ready_98
    push 98
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_98:
    jmp eax
_winmm_stub_98 ENDP
_winmm_stub_99 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 396]
    test eax, eax
    jne winmm_ready_99
    push 99
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_99:
    jmp eax
_winmm_stub_99 ENDP
_winmm_stub_100 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 400]
    test eax, eax
    jne winmm_ready_100
    push 100
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_100:
    jmp eax
_winmm_stub_100 ENDP
_winmm_stub_101 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 404]
    test eax, eax
    jne winmm_ready_101
    push 101
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_101:
    jmp eax
_winmm_stub_101 ENDP
_winmm_stub_102 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 408]
    test eax, eax
    jne winmm_ready_102
    push 102
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_102:
    jmp eax
_winmm_stub_102 ENDP
_winmm_stub_103 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 412]
    test eax, eax
    jne winmm_ready_103
    push 103
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_103:
    jmp eax
_winmm_stub_103 ENDP
_winmm_stub_104 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 416]
    test eax, eax
    jne winmm_ready_104
    push 104
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_104:
    jmp eax
_winmm_stub_104 ENDP
_winmm_stub_105 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 420]
    test eax, eax
    jne winmm_ready_105
    push 105
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_105:
    jmp eax
_winmm_stub_105 ENDP
_winmm_stub_106 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 424]
    test eax, eax
    jne winmm_ready_106
    push 106
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_106:
    jmp eax
_winmm_stub_106 ENDP
_winmm_stub_107 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 428]
    test eax, eax
    jne winmm_ready_107
    push 107
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_107:
    jmp eax
_winmm_stub_107 ENDP
_winmm_stub_108 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 432]
    test eax, eax
    jne winmm_ready_108
    push 108
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_108:
    jmp eax
_winmm_stub_108 ENDP
_winmm_stub_109 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 436]
    test eax, eax
    jne winmm_ready_109
    push 109
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_109:
    jmp eax
_winmm_stub_109 ENDP
_winmm_stub_110 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 440]
    test eax, eax
    jne winmm_ready_110
    push 110
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_110:
    jmp eax
_winmm_stub_110 ENDP
_winmm_stub_111 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 444]
    test eax, eax
    jne winmm_ready_111
    push 111
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_111:
    jmp eax
_winmm_stub_111 ENDP
_winmm_stub_112 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 448]
    test eax, eax
    jne winmm_ready_112
    push 112
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_112:
    jmp eax
_winmm_stub_112 ENDP
_winmm_stub_113 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 452]
    test eax, eax
    jne winmm_ready_113
    push 113
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_113:
    jmp eax
_winmm_stub_113 ENDP
_winmm_stub_114 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 456]
    test eax, eax
    jne winmm_ready_114
    push 114
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_114:
    jmp eax
_winmm_stub_114 ENDP
_winmm_stub_115 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 460]
    test eax, eax
    jne winmm_ready_115
    push 115
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_115:
    jmp eax
_winmm_stub_115 ENDP
_winmm_stub_116 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 464]
    test eax, eax
    jne winmm_ready_116
    push 116
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_116:
    jmp eax
_winmm_stub_116 ENDP
_winmm_stub_117 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 468]
    test eax, eax
    jne winmm_ready_117
    push 117
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_117:
    jmp eax
_winmm_stub_117 ENDP
_winmm_stub_118 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 472]
    test eax, eax
    jne winmm_ready_118
    push 118
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_118:
    jmp eax
_winmm_stub_118 ENDP
_winmm_stub_119 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 476]
    test eax, eax
    jne winmm_ready_119
    push 119
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_119:
    jmp eax
_winmm_stub_119 ENDP
_winmm_stub_120 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 480]
    test eax, eax
    jne winmm_ready_120
    push 120
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_120:
    jmp eax
_winmm_stub_120 ENDP
_winmm_stub_121 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 484]
    test eax, eax
    jne winmm_ready_121
    push 121
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_121:
    jmp eax
_winmm_stub_121 ENDP
_winmm_stub_122 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 488]
    test eax, eax
    jne winmm_ready_122
    push 122
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_122:
    jmp eax
_winmm_stub_122 ENDP
_winmm_stub_123 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 492]
    test eax, eax
    jne winmm_ready_123
    push 123
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_123:
    jmp eax
_winmm_stub_123 ENDP
_winmm_stub_124 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 496]
    test eax, eax
    jne winmm_ready_124
    push 124
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_124:
    jmp eax
_winmm_stub_124 ENDP
_winmm_stub_125 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 500]
    test eax, eax
    jne winmm_ready_125
    push 125
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_125:
    jmp eax
_winmm_stub_125 ENDP
_winmm_stub_126 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 504]
    test eax, eax
    jne winmm_ready_126
    push 126
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_126:
    jmp eax
_winmm_stub_126 ENDP
_winmm_stub_127 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 508]
    test eax, eax
    jne winmm_ready_127
    push 127
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_127:
    jmp eax
_winmm_stub_127 ENDP
_winmm_stub_128 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 512]
    test eax, eax
    jne winmm_ready_128
    push 128
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_128:
    jmp eax
_winmm_stub_128 ENDP
_winmm_stub_129 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 516]
    test eax, eax
    jne winmm_ready_129
    push 129
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_129:
    jmp eax
_winmm_stub_129 ENDP
_winmm_stub_130 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 520]
    test eax, eax
    jne winmm_ready_130
    push 130
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_130:
    jmp eax
_winmm_stub_130 ENDP
_winmm_stub_131 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 524]
    test eax, eax
    jne winmm_ready_131
    push 131
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_131:
    jmp eax
_winmm_stub_131 ENDP
_winmm_stub_132 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 528]
    test eax, eax
    jne winmm_ready_132
    push 132
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_132:
    jmp eax
_winmm_stub_132 ENDP
_winmm_stub_133 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 532]
    test eax, eax
    jne winmm_ready_133
    push 133
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_133:
    jmp eax
_winmm_stub_133 ENDP
_winmm_stub_134 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 536]
    test eax, eax
    jne winmm_ready_134
    push 134
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_134:
    jmp eax
_winmm_stub_134 ENDP
_winmm_stub_135 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 540]
    test eax, eax
    jne winmm_ready_135
    push 135
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_135:
    jmp eax
_winmm_stub_135 ENDP
_winmm_stub_136 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 544]
    test eax, eax
    jne winmm_ready_136
    push 136
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_136:
    jmp eax
_winmm_stub_136 ENDP
_winmm_stub_137 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 548]
    test eax, eax
    jne winmm_ready_137
    push 137
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_137:
    jmp eax
_winmm_stub_137 ENDP
_winmm_stub_138 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 552]
    test eax, eax
    jne winmm_ready_138
    push 138
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_138:
    jmp eax
_winmm_stub_138 ENDP
_winmm_stub_139 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 556]
    test eax, eax
    jne winmm_ready_139
    push 139
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_139:
    jmp eax
_winmm_stub_139 ENDP
_winmm_stub_140 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 560]
    test eax, eax
    jne winmm_ready_140
    push 140
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_140:
    jmp eax
_winmm_stub_140 ENDP
_winmm_stub_141 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 564]
    test eax, eax
    jne winmm_ready_141
    push 141
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_141:
    jmp eax
_winmm_stub_141 ENDP
_winmm_stub_142 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 568]
    test eax, eax
    jne winmm_ready_142
    push 142
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_142:
    jmp eax
_winmm_stub_142 ENDP
_winmm_stub_143 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 572]
    test eax, eax
    jne winmm_ready_143
    push 143
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_143:
    jmp eax
_winmm_stub_143 ENDP
_winmm_stub_144 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 576]
    test eax, eax
    jne winmm_ready_144
    push 144
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_144:
    jmp eax
_winmm_stub_144 ENDP
_winmm_stub_145 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 580]
    test eax, eax
    jne winmm_ready_145
    push 145
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_145:
    jmp eax
_winmm_stub_145 ENDP
_winmm_stub_146 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 584]
    test eax, eax
    jne winmm_ready_146
    push 146
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_146:
    jmp eax
_winmm_stub_146 ENDP
_winmm_stub_147 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 588]
    test eax, eax
    jne winmm_ready_147
    push 147
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_147:
    jmp eax
_winmm_stub_147 ENDP
_winmm_stub_148 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 592]
    test eax, eax
    jne winmm_ready_148
    push 148
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_148:
    jmp eax
_winmm_stub_148 ENDP
_winmm_stub_149 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 596]
    test eax, eax
    jne winmm_ready_149
    push 149
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_149:
    jmp eax
_winmm_stub_149 ENDP
_winmm_stub_150 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 600]
    test eax, eax
    jne winmm_ready_150
    push 150
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_150:
    jmp eax
_winmm_stub_150 ENDP
_winmm_stub_151 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 604]
    test eax, eax
    jne winmm_ready_151
    push 151
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_151:
    jmp eax
_winmm_stub_151 ENDP
_winmm_stub_152 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 608]
    test eax, eax
    jne winmm_ready_152
    push 152
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_152:
    jmp eax
_winmm_stub_152 ENDP
_winmm_stub_153 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 612]
    test eax, eax
    jne winmm_ready_153
    push 153
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_153:
    jmp eax
_winmm_stub_153 ENDP
_winmm_stub_154 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 616]
    test eax, eax
    jne winmm_ready_154
    push 154
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_154:
    jmp eax
_winmm_stub_154 ENDP
_winmm_stub_155 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 620]
    test eax, eax
    jne winmm_ready_155
    push 155
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_155:
    jmp eax
_winmm_stub_155 ENDP
_winmm_stub_156 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 624]
    test eax, eax
    jne winmm_ready_156
    push 156
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_156:
    jmp eax
_winmm_stub_156 ENDP
_winmm_stub_157 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 628]
    test eax, eax
    jne winmm_ready_157
    push 157
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_157:
    jmp eax
_winmm_stub_157 ENDP
_winmm_stub_158 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 632]
    test eax, eax
    jne winmm_ready_158
    push 158
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_158:
    jmp eax
_winmm_stub_158 ENDP
_winmm_stub_159 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 636]
    test eax, eax
    jne winmm_ready_159
    push 159
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_159:
    jmp eax
_winmm_stub_159 ENDP
_winmm_stub_160 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 640]
    test eax, eax
    jne winmm_ready_160
    push 160
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_160:
    jmp eax
_winmm_stub_160 ENDP
_winmm_stub_161 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 644]
    test eax, eax
    jne winmm_ready_161
    push 161
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_161:
    jmp eax
_winmm_stub_161 ENDP
_winmm_stub_162 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 648]
    test eax, eax
    jne winmm_ready_162
    push 162
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_162:
    jmp eax
_winmm_stub_162 ENDP
_winmm_stub_163 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 652]
    test eax, eax
    jne winmm_ready_163
    push 163
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_163:
    jmp eax
_winmm_stub_163 ENDP
_winmm_stub_164 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 656]
    test eax, eax
    jne winmm_ready_164
    push 164
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_164:
    jmp eax
_winmm_stub_164 ENDP
_winmm_stub_165 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 660]
    test eax, eax
    jne winmm_ready_165
    push 165
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_165:
    jmp eax
_winmm_stub_165 ENDP
_winmm_stub_166 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 664]
    test eax, eax
    jne winmm_ready_166
    push 166
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_166:
    jmp eax
_winmm_stub_166 ENDP
_winmm_stub_167 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 668]
    test eax, eax
    jne winmm_ready_167
    push 167
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_167:
    jmp eax
_winmm_stub_167 ENDP
_winmm_stub_168 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 672]
    test eax, eax
    jne winmm_ready_168
    push 168
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_168:
    jmp eax
_winmm_stub_168 ENDP
_winmm_stub_169 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 676]
    test eax, eax
    jne winmm_ready_169
    push 169
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_169:
    jmp eax
_winmm_stub_169 ENDP
_winmm_stub_170 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 680]
    test eax, eax
    jne winmm_ready_170
    push 170
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_170:
    jmp eax
_winmm_stub_170 ENDP
_winmm_stub_171 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 684]
    test eax, eax
    jne winmm_ready_171
    push 171
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_171:
    jmp eax
_winmm_stub_171 ENDP
_winmm_stub_172 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 688]
    test eax, eax
    jne winmm_ready_172
    push 172
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_172:
    jmp eax
_winmm_stub_172 ENDP
_winmm_stub_173 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 692]
    test eax, eax
    jne winmm_ready_173
    push 173
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_173:
    jmp eax
_winmm_stub_173 ENDP
_winmm_stub_174 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 696]
    test eax, eax
    jne winmm_ready_174
    push 174
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_174:
    jmp eax
_winmm_stub_174 ENDP
_winmm_stub_175 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 700]
    test eax, eax
    jne winmm_ready_175
    push 175
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_175:
    jmp eax
_winmm_stub_175 ENDP
_winmm_stub_176 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 704]
    test eax, eax
    jne winmm_ready_176
    push 176
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_176:
    jmp eax
_winmm_stub_176 ENDP
_winmm_stub_177 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 708]
    test eax, eax
    jne winmm_ready_177
    push 177
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_177:
    jmp eax
_winmm_stub_177 ENDP
_winmm_stub_178 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 712]
    test eax, eax
    jne winmm_ready_178
    push 178
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_178:
    jmp eax
_winmm_stub_178 ENDP
_winmm_stub_179 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 716]
    test eax, eax
    jne winmm_ready_179
    push 179
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_179:
    jmp eax
_winmm_stub_179 ENDP
_winmm_stub_180 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 720]
    test eax, eax
    jne winmm_ready_180
    push 180
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_180:
    jmp eax
_winmm_stub_180 ENDP
_winmm_stub_181 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 724]
    test eax, eax
    jne winmm_ready_181
    push 181
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_181:
    jmp eax
_winmm_stub_181 ENDP
_winmm_stub_182 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 728]
    test eax, eax
    jne winmm_ready_182
    push 182
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_182:
    jmp eax
_winmm_stub_182 ENDP
_winmm_stub_183 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 732]
    test eax, eax
    jne winmm_ready_183
    push 183
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_183:
    jmp eax
_winmm_stub_183 ENDP
_winmm_stub_184 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 736]
    test eax, eax
    jne winmm_ready_184
    push 184
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_184:
    jmp eax
_winmm_stub_184 ENDP
_winmm_stub_185 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 740]
    test eax, eax
    jne winmm_ready_185
    push 185
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_185:
    jmp eax
_winmm_stub_185 ENDP
_winmm_stub_186 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 744]
    test eax, eax
    jne winmm_ready_186
    push 186
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_186:
    jmp eax
_winmm_stub_186 ENDP
_winmm_stub_187 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 748]
    test eax, eax
    jne winmm_ready_187
    push 187
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_187:
    jmp eax
_winmm_stub_187 ENDP
_winmm_stub_188 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 752]
    test eax, eax
    jne winmm_ready_188
    push 188
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_188:
    jmp eax
_winmm_stub_188 ENDP
_winmm_stub_189 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 756]
    test eax, eax
    jne winmm_ready_189
    push 189
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_189:
    jmp eax
_winmm_stub_189 ENDP
_winmm_stub_190 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 760]
    test eax, eax
    jne winmm_ready_190
    push 190
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_190:
    jmp eax
_winmm_stub_190 ENDP
_winmm_stub_191 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 764]
    test eax, eax
    jne winmm_ready_191
    push 191
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_191:
    jmp eax
_winmm_stub_191 ENDP
_winmm_stub_192 PROC
    mov eax, DWORD PTR [_g_winmm_functions + 768]
    test eax, eax
    jne winmm_ready_192
    push 192
    call _ResolveWinmmExport
    add esp, 4
winmm_ready_192:
    jmp eax
_winmm_stub_192 ENDP
END
