import sys
import json

def get_status(printers):
    import win32print
    result = {}
    for pname in printers:
        try:
            h = win32print.OpenPrinter(pname)
            try:
                info = win32print.GetPrinter(h, 2)
                st = info.get('Status', 0)
                jobs_count = info.get('cJobs', 0)
                
                jobs = []
                if jobs_count > 0:
                    raw_jobs = win32print.EnumJobs(h, 0, -1, 2)
                    for j in raw_jobs:
                        jobs.append({
                            'TotalPages': j.get('TotalPages', 0),
                            'PagesPrinted': j.get('PagesPrinted', 0),
                            'Size': j.get('Size', 0)
                        })
                
                result[pname] = {
                    'status': st,
                    'jobs_count': jobs_count,
                    'jobs': jobs
                }
            finally:
                win32print.ClosePrinter(h)
        except Exception as e:
            result[pname] = {'error': str(e)}
            
    print(json.dumps(result))

if __name__ == '__main__':
    if len(sys.argv) > 1:
        get_status(json.loads(sys.argv[1]))
