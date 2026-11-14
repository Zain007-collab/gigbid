# cleanup-real-estate-14 (synthetic)

=XLOOKUP(A2, Orders!A:A, Orders!F:F)

function refresh() { SpreadsheetApp.getActive().getSheetByName('KPI') }

import pandas as pd
df = pd.read_csv(path).drop_duplicates()

def main() -> None:
