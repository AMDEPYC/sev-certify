from cpuid import cpuid
import argparse

def get_cpuid(function, register) -> int:
    '''
    Get register value for a cpuid funtion
    '''
    registers = ['eax', 'ebx', 'ecx', 'edx']

    try:
        # read the cpuid function
        register_values = cpuid(function)
    # If some value error, then return none (cpuid failed)
    except ValueError:
        return None

    # Create a dict of the register name matched with the value
    reg_dict = dict(zip(registers, register_values))

    # Return the desired registerss
    return reg_dict[register]

def get_processor_model():
    '''
    Get the processor model name from the cpuid.
    '''
    # Read eax register from function 0x80000001
    eax = get_cpuid(0x80000001, 'eax')

    # eax read failed return none
    if eax is None:
        return None

    # Generate bin value
    bin_value = bin(eax)[2:][::-1]

    # Base family bits [11:8]
    base_family = int(bin_value[8:12][::-1],2)
    # Extended family bits [27:20]
    extended_family = int(bin_value[20:28][::-1],2)

    # Base model bits [7:4]
    base_model = bin_value[4:8][::-1]
    # Extended model bits [19:16]
    extended_model = bin_value[16:20][::-1]

    # Family = base family + extended family
    family = base_family + extended_family
    # Model = extended_model:base model
    model = int(extended_model + base_model, 2)

    # Match family and model to known values
    if family == 23 and ( 0 <= model <= 15) :
        codename = 'Naples', '7001'
    elif family == 23 and (48 <= model <= 63):
        codename = 'Rome', '7002'
    elif family == 25 and (0 <= model <= 15):
        codename = 'Milan', '7003'
    elif family == 25 and ((16 <= model <= 31) or (160 <= model <= 175)):
        codename = 'Genoa', '9004'
    elif family == 26 and (0 <= model <= 17):
        codename = 'Turin', '9005'
    else:
        codename = 'invalid cpu'

    return codename

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Get AMD processor model information')
    parser.add_argument('field', nargs='?', choices=['codename', 'series'], 
                       help='Field to return: codename (e.g., "Naples") or series (e.g., "7001")')
    
    args = parser.parse_args()
    
    result = get_processor_model()
    
    if result == 'invalid cpu':
        print(result)
    elif args.field == 'codename':
        print(result[0])
    elif args.field == 'series':
        print(result[1])
    else:
        # No argument: return both
        print(f"{result[0]} {result[1]}")

