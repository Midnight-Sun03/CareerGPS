from core import models as m
print('CareerMarketSnapshot', m.CareerMarketSnapshot.objects.count())
print('CareerMarketSnapshotOpportunity', m.CareerMarketSnapshotOpportunity.objects.count())
print('Opportunity', m.Opportunity.objects.count())
print('OpportunityExtraction status', dict(__import__('collections').Counter(m.OpportunityExtraction.objects.values_list('status', flat=True))))
print('CareerPreference', m.CareerPreference.objects.count())
print('DONE4')
