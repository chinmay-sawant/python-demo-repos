from django.urls import path

from . import views

urlpatterns = [
    path('skus/<str:sku_code>/availability/', views.sku_availability, name='sku-availability'),
    path('availability/batch/', views.batch_availability, name='batch-availability'),
    path('warehouses/<str:warehouse_code>/rollup/', views.warehouse_rollup, name='warehouse-rollup'),
]
