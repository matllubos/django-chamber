from __future__ import unicode_literals

import collections

from itertools import chain

from six import python_2_unicode_compatible

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.translation import ugettext_lazy as _
from django.utils.encoding import force_text

from chamber.exceptions import PersistenceException
from chamber.patch import Options

from .fields import *  #  NOQA exposing classes and functions as a module API
from .patch import *  # NOQA


def many_to_many_field_to_dict(field, instance):
    if instance.pk is None:
        # If the object doesn't have a primary key yet, just use an empty
        # list for its m2m fields. Calling f.value_from_object will raise
        # an exception.
        return []
    else:
        # MultipleChoiceWidget needs a list of pks, not object instances.
        return list(field.value_from_object(instance).values_list('pk', flat=True))


def should_exclude_field(field, fields, exclude):
    return (fields and field.name not in fields) or (exclude and field.name in exclude)


def field_to_dict(field, instance):
    """
    Converts a model field to a dictionary
    """
    # avoid a circular import
    from django.db.models.fields.related import ManyToManyField

    return (many_to_many_field_to_dict(field, instance) if isinstance(field, ManyToManyField)
            else field.value_from_object(instance))


def model_to_dict(instance, fields=None, exclude=None):
    """
    The same implementation as django model_to_dict but editable fields are allowed
    """

    return {
        field.name: field_to_dict(field, instance)
        for field in chain(instance._meta.concrete_fields, instance._meta.many_to_many)  # pylint: disable=W0212
        if not should_exclude_field(field, fields, exclude)
    }


ValueChange = collections.namedtuple('ValueChange', ('initial', 'current'))


@python_2_unicode_compatible
class ChangedFields(object):

    def __init__(self, instance):
        self.instance = instance
        self.initial_values = self.get_instance_dict(instance)

    def get_instance_dict(self, instance):
        return model_to_dict(instance, fields=(field.name for field in instance._meta.fields))

    @property
    def diff(self):
        d1 = self.initial_values
        d2 = self.get_instance_dict(self.instance)
        return {k: ValueChange(v, d2[k]) for k, v in d1.items() if v != d2[k]}

    def __setitem__(self, key, item):
        raise AttributeError('Object is readonly')

    def __getitem__(self, key):
        return self.diff[key]

    def __repr__(self):
        return repr(self.diff)

    def __len__(self):
        return len(self.diff)

    def __delitem__(self, key):
        raise AttributeError('Object is readonly')

    def clear(self):
        raise AttributeError('Object is readonly')

    def has_key(self, k):
        return k in self.diff

    def has_any_key(self, *keys):
        return bool(set(self.keys()) & set(keys))

    def update(self, *args, **kwargs):
        raise AttributeError('Object is readonly')

    def keys(self):
        return self.diff.keys()

    def values(self):
        return self.diff.values()

    def items(self):
        return self.diff.items()

    def pop(self, *args, **kwargs):
        raise AttributeError('Object is readonly')

    def __cmp__(self, dictionary):
        return cmp(self.diff, dictionary)

    def __contains__(self, item):
        return item in self.diff

    def __iter__(self):
        return iter(self.diff)

    def __str__(self):
        return repr(self.diff)


class ComparableModelMixin(object):

    def equals(self, obj, comparator):
        """
        Use comparator for evaluating if objects are the same
        """
        return comparator.compare(self, obj)


class Comparator(object):

    def compare(self, a, b):
        """
        Return True if objects are same otherwise False
        """
        raise NotImplementedError


class AuditModel(models.Model):
    created_at = models.DateTimeField(verbose_name=_('created at'), null=False, blank=False, auto_now_add=True,
                                      db_index=True)
    changed_at = models.DateTimeField(verbose_name=_('changed at'), null=False, blank=False, auto_now=True,
                                      db_index=True)

    class Meta:
        abstract = True


class Signal(object):

    def __init__(self, obj):
        self.connected_functions = []
        self.obj = obj

    def connect(self, fun):
        self.connected_functions.append(fun)

    def send(self):
        [fun(self.obj) for fun in self.connected_functions]


class SmartQuerySet(models.QuerySet):

    def fast_distinct(self):
        return self.model.objects.filter(pk__in=self.values_list('pk', flat=True))


class SmartModel(AuditModel):

    objects = SmartQuerySet.as_manager()

    def __init__(self, *args, **kwargs):
        super(SmartModel, self).__init__(*args, **kwargs)
        self.changed_fields = ChangedFields(self)
        self.post_save = Signal(self)

    @property
    def has_changed(self):
        return bool(self.changed_fields)

    @property
    def initial_values(self):
        return self.changed_fields.initial_values

    def full_clean(self, exclude=None, *args, **kwargs):
        errors = {}
        for field in self._meta.fields:
            if (not exclude or field.name not in exclude) and hasattr(self, 'clean_{}'.format(field.name)):
                try:
                    getattr(self, 'clean_{}'.format(field.name))()
                except ValidationError as er:
                    errors[field.name] = er.messages

        if errors:
            raise ValidationError(errors)
        super(SmartModel, self).full_clean(exclude=exclude, *args, **kwargs)

    def _clean_save(self, *args, **kwargs):
        self._persistence_clean(*args, **kwargs)

    def _clean_delete(self, *args, **kwargs):
        self._persistence_clean(*args, **kwargs)

    def _clean_pre_save(self, *args, **kwargs):
        self._clean_save(*args, **kwargs)

    def _clean_pre_delete(self, *args, **kwargs):
        self._clean_delete(*args, **kwargs)

    def _clean_post_save(self, *args, **kwargs):
        self._clean_save(*args, **kwargs)

    def _clean_post_delete(self, *args, **kwargs):
        self._clean_delete(*args, **kwargs)

    def _persistence_clean(self, *args, **kwargs):
        exclude = kwargs.pop('exclude', None)
        try:
            self.full_clean(exclude=exclude)
        except ValidationError as er:
            if hasattr(er, 'error_dict'):
                raise PersistenceException(', '.join(
                    ('%s: %s' % (key, ', '.join(map(force_text, val))) for key, val in er.message_dict.items())))
            else:
                raise PersistenceException(', '.join(map(force_text, er.messages)))

    def _get_save_extra_kwargs(self):
        return {}

    def _pre_save(self, *args, **kwargs):
        pass

    def _call_dispatcher_group(self, group_name, change, changed_fields, *args, **kwargs):
        if hasattr(self, group_name):
            for dispatcher in getattr(self, group_name):
                dispatcher(self, change, changed_fields, *args, **kwargs)

    def _save(self, is_cleaned_pre_save=None, is_cleaned_post_save=None, force_insert=False, force_update=False,
              using=None, update_fields=None, *args, **kwargs):
        is_cleaned_pre_save = (
            self._smart_meta.is_cleaned_pre_save if is_cleaned_pre_save is None else is_cleaned_pre_save
        )
        is_cleaned_post_save = (
            self._smart_meta.is_cleaned_post_save if is_cleaned_post_save is None else is_cleaned_post_save
        )

        change = bool(self.pk)
        kwargs.update(self._get_save_extra_kwargs())

        self._pre_save(change, self.changed_fields, *args, **kwargs)
        self._call_dispatcher_group('pre_save_dispatchers', change, self.changed_fields, *args, **kwargs)

        if is_cleaned_pre_save:
            self._clean_pre_save(*args, **kwargs)

        super(SmartModel, self).save(force_insert=force_insert, force_update=force_update, using=using,
                                     update_fields=update_fields)
        self._post_save(change, self.changed_fields, *args, **kwargs)
        self._call_dispatcher_group('post_save_dispatchers', change, self.changed_fields, *args, **kwargs)

        if is_cleaned_post_save:
            self._clean_post_save(*args, **kwargs)
        self.post_save.send()

    def _post_save(self, *args, **kwargs):
        pass

    def save(self, *args, **kwargs):
        if self._smart_meta.is_save_atomic:
            with transaction.atomic():
                self._save(*args, **kwargs)
        else:
            self._save(*args, **kwargs)
        self.changed_fields = ChangedFields(self)

    def _pre_delete(self, *args, **kwargs):
        pass

    def _delete(self, is_cleaned_pre_delete=None, is_cleaned_post_delete=None, *args, **kwargs):
        is_cleaned_pre_delete = (
            self._smart_meta.is_cleaned_pre_delete if is_cleaned_pre_delete is None else is_cleaned_pre_delete
        )
        is_cleaned_post_delete = (
            self._smart_meta.is_cleaned_post_delete if is_cleaned_post_delete is None else is_cleaned_post_delete
        )

        self._pre_delete(*args, **kwargs)

        if is_cleaned_pre_delete:
            self._clean_pre_delete(*args, **kwargs)

        super(SmartModel, self).delete(*args, **kwargs)

        self._post_delete(*args, **kwargs)

        if is_cleaned_post_delete:
            self._clean_post_delete(*args, **kwargs)

    def _post_delete(self, *args, **kwargs):
        pass

    def delete(self, *args, **kwargs):
        if self._smart_meta.is_delete_atomic:
            with transaction.atomic():
                self._delete(*args, **kwargs)
        else:
            self._delete(*args, **kwargs)

    class Meta:
        abstract = True


class SmartOptions(Options):

    meta_class_name = 'SmartMeta'
    meta_name = '_smart_meta'
    model_class = SmartModel
    attributes = {
        'is_cleaned_pre_save': True,
        'is_cleaned_post_save': False,
        'is_cleaned_pre_delete': False,
        'is_cleaned_post_delete': False,
        'is_save_atomic': False,
        'is_delete_atomic': False,
    }
