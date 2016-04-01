/** @file

  Copyright (c) 2016, Canonical Limited. All rights reserved.

  This program and the accompanying materials                          
  are licensed and made available under the terms and conditions of the BSD License         
  which accompanies this distribution.  The full text of the license may be found at        
  http://opensource.org/licenses/bsd-license.php                                            

  THE PROGRAM IS DISTRIBUTED UNDER THE BSD LICENSE ON AN "AS IS" BASIS,                     
  WITHOUT WARRANTIES OR REPRESENTATIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED.             

**/

#include <Uefi.h>
#include <Library/UefiLib.h>
#include <Library/UefiApplicationEntryPoint.h>

#define  MOKSBSTATE_GUID    \
{ 0x605DAB50, 0xE046, 0x4300, {0xab, 0xb6, 0x3d, 0xd8, 0x10, 0xdd, 0x8b, 0x23}}

EFI_STATUS
EFIAPI
UefiMain (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  UINT32        VariableAttr;
  EFI_GUID      VariableMoksbGuid = MOKSBSTATE_GUID;
  UINT8         Data=1;

  VariableAttr = (EFI_VARIABLE_NON_VOLATILE|EFI_VARIABLE_BOOTSERVICE_ACCESS);	

  SystemTable->RuntimeServices->SetVariable (
         L"MokSBState",     
         &VariableMoksbGuid,   
         VariableAttr,        
         1, 
         &Data          
         );

  return EFI_SUCCESS;
}
