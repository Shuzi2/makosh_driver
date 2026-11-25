"""Driver expense totals retain original currency and explicit conversions."""
from decimal import Decimal

def summarize(expenses):
    totals={}
    nonfuel=Decimal('0')
    missing=False
    for expense in expenses:
        totals[expense.currency]=totals.get(expense.currency,Decimal('0'))+expense.amount
        if expense.category=='fuel':
            continue
        value=expense.amount if expense.currency=='UAH' else expense.converted_uah
        if value is None: missing=True
        else: nonfuel+=value
    return {'totals':[{'currency':key,'amount':value} for key,value in sorted(totals.items())], 'nonfuel_uah':nonfuel,'missing_conversion':missing}
